import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  CalendarClock,
  ChevronRight,
  FileText,
  Mail,
  RefreshCw,
  Send,
  Sparkles,
  Timer,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import Button from '../components/ui/Button'
import Empty from '../components/ui/Feedback'
import Modal from '../components/ui/Modal'
import { useToast } from '../components/ui/Toast'
import MarkdownView from '../components/notes/MarkdownView'
import {
  createReviewReport,
  deleteReviewReport,
  getReviewReport,
  getSchedule,
  getPushState,
  getSettings,
  listReviewReports,
  pushReviewReport,
  runScheduleNow,
  type PushStatus,
  type ReportBrief,
  type ReportDetail,
  type ReportWindow,
  type ScheduleState,
} from '../api/client'
import HelpTip from '../components/ui/HelpTip'

const WINDOWS: Array<{ value: ReportWindow; label: string }> = [
  { value: 7, label: '近 7 天' },
  { value: 14, label: '近 14 天' },
  { value: 30, label: '近 30 天' },
]

const WEEKDAYS: Record<string, string> = {
  mon: '周一',
  tue: '周二',
  wed: '周三',
  thu: '周四',
  fri: '周五',
  sat: '周六',
  sun: '周日',
}

const PUSH_LABEL: Record<PushStatus, string> = {
  sent: '已推送',
  failed: '推送失败',
  pending: '待推送',
  skipped: '未推送',
  none: '—',
}

const SOURCE_LABEL: Record<string, string> = {
  llm: '模型分析',
  rule: '规则分析',
  empty: '数据不足',
}

function fmtDay(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10)
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`
}

function fmtClock(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16).replace('T', ' ')
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(
    date.getMinutes()
  ).padStart(2, '0')}`
}

/** 回顾页：生成、查看、推送报告（FR-07 / FR-08）。 */
export default function Review() {
  const toast = useToast()
  const [reports, setReports] = useState<ReportBrief[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ReportDetail | null>(null)
  const [windowDays, setWindowDays] = useState<ReportWindow>(7)
  const [notify, setNotify] = useState(false)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [busy, setBusy] = useState<'push' | 'delete' | 'run' | null>(null)
  const [schedule, setSchedule] = useState<ScheduleState | null>(null)
  const [smtpReady, setSmtpReady] = useState(false)
  const [pushState, setPushState] = useState<{ status: PushStatus; detail: string }>({
    status: 'none',
    detail: '',
  })
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [list, state] = await Promise.all([listReviewReports(), getPushState()])
      setReports(list)
      setPushState({ status: state.status, detail: state.detail })
      setActiveId((current) => current ?? list[0]?.id ?? null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void load()
    getSchedule().then(setSchedule).catch(() => setSchedule(null))
    // 没配 SMTP 时「同时发送」禁用，否则用户点完只会拿到一条失败提示
    getSettings()
      .then((s) => setSmtpReady(s.smtp.configured))
      .catch(() => setSmtpReady(false))
  }, [load])

  useEffect(() => {
    if (!activeId) {
      setDetail(null)
      return
    }
    let cancelled = false
    getReviewReport(activeId)
      .then((data) => {
        if (!cancelled) setDetail(data)
      })
      .catch(() => {
        if (!cancelled) setDetail(null)
      })
    return () => {
      cancelled = true
    }
  }, [activeId])

  const generate = async () => {
    setGenerating(true)
    try {
      const created = await createReviewReport({ window_days: windowDays, notify })
      toast.success(`已生成最近 ${windowDays} 天的回顾`)
      if (notify && created.push_status === 'failed') {
        toast.error(`生成成功，但推送失败：${created.push_detail}`)
      }
      await load()
      setActiveId(created.id)
      setDetail(created)
      getSchedule().then(setSchedule).catch(() => undefined)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '生成失败')
    } finally {
      setGenerating(false)
    }
  }

  const push = async (id: string) => {
    setBusy('push')
    try {
      const result = await pushReviewReport(id)
      if (result.ok) {
        toast.success(result.detail)
      } else {
        toast.error(`推送失败：${result.detail}`)
      }
      setDetail(result.report)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '推送失败')
    } finally {
      setBusy(null)
    }
  }

  const remove = async (id: string) => {
    setBusy('delete')
    try {
      await deleteReviewReport(id)
      setConfirmId(null)
      if (activeId === id) {
        setActiveId(null)
        setDetail(null)
      }
      await load()
      toast.success('已删除')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败')
    } finally {
      setBusy(null)
    }
  }

  const runNow = async () => {
    setBusy('run')
    try {
      const result = await runScheduleNow()
      toast.success(result.message)
      await load()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '执行失败')
    } finally {
      setBusy(null)
    }
  }

  const scheduleText = schedule
    ? `每${WEEKDAYS[schedule.cron.day_of_week] ?? schedule.cron.day_of_week} ${String(
        schedule.cron.hour
      ).padStart(2, '0')}:${String(schedule.cron.minute).padStart(2, '0')}`
    : '—'

  const nextRun = useMemo(() => {
    if (!schedule?.next_run) return ''
    return fmtClock(schedule.next_run)
  }, [schedule])

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>回顾</h1>
          <p className="page-sub">
            把这段时间的学习整理成一份报告。
            <HelpTip label="了解报告内容">
              一份报告包含：这段时间新增了哪些资料、覆盖了什么主题；练习套数与正确率的变化；
              错得最多的知识点；薄弱点判断与下一步建议。
              数字由程序统计，只有分析段落由模型撰写，模型不可用时会退化成规则版并在报告里注明。
            </HelpTip>
          </p>
        </div>
        <div className="page-actions rv-actions">
          <div className="rv-window">
            {WINDOWS.map((item) => (
              <button
                key={item.value}
                className="chip"
                data-on={windowDays === item.value}
                onClick={() => setWindowDays(item.value)}
                disabled={generating}
              >
                {item.label}
              </button>
            ))}
          </div>
          <label
            className="rv-notify"
            data-off={!smtpReady}
            title={
              smtpReady ? '生成后立刻发到设置里的收件邮箱' : '还没有配置 SMTP，先去设置页填写'
            }
          >
            <input
              type="checkbox"
              checked={notify && smtpReady}
              onChange={(e) => setNotify(e.target.checked)}
              disabled={generating || !smtpReady}
            />
            同时发送
          </label>
          <Button
            variant="primary"
            icon={<Sparkles size={14} />}
            loading={generating}
            onClick={() => void generate()}
          >
            {generating ? '正在生成…' : '生成回顾'}
          </Button>
        </div>
      </header>

      {generating && (
        <div className="notice rv-generating">
          <Sparkles />
          <span>正在读取最近 {windowDays} 天的数据，通常 10~30 秒。</span>
        </div>
      )}

      {pushState.status === 'failed' && (
        <div className="notice rv-alert">
          <TriangleAlert />
          <span>
            上次推送失败：{pushState.detail || '原因未记录'}。报告已经保存在本地，可以重新推送。
          </span>
        </div>
      )}

      <div className="workbench">
        <div className="card">
          <div className="card-head">
            <h2>
              <FileText size={16} /> 历史报告
            </h2>
            <span className="head-note">{loading ? '加载中…' : `${reports.length} 份`}</span>
          </div>

          {!loading && reports.length === 0 ? (
            <Empty
              icon={<CalendarClock size={18} />}
              title="还没有生成过回顾"
              description="导入资料并做几套练习后，报告会更有参考价值。"
            >
              <Button size="sm" onClick={() => void generate()} loading={generating}>
                试生成一份
              </Button>
            </Empty>
          ) : (
            <div className="rv-list">
              {reports.map((item) => (
                <button
                  key={item.id}
                  className="rv-item"
                  data-on={item.id === activeId}
                  onClick={() => setActiveId(item.id)}
                >
                  <div className="rv-item-head">
                    <span className="rv-item-title">最近 {item.window_days} 天</span>
                    {item.trigger === 'schedule' && <span className="rv-tag">定时</span>}
                    {item.summary.analysis_source === 'rule' && (
                      <span className="rv-tag" data-kind="muted">
                        规则
                      </span>
                    )}
                    <ChevronRight size={14} className="rv-item-arrow" />
                  </div>
                  <div className="rv-item-meta">
                    {fmtDay(item.period_start)} → {fmtDay(item.period_end)}
                    <span className="rv-dot">·</span>
                    {fmtClock(item.created_at)}
                  </div>
                  <div className="rv-item-stats">
                    <span>{item.summary.documents} 份资料</span>
                    <span>{item.summary.sessions} 套练习</span>
                    <span
                      data-tone={
                        item.summary.answered === 0
                          ? 'muted'
                          : item.summary.accuracy >= 70
                            ? 'good'
                            : 'warn'
                      }
                    >
                      {item.summary.answered === 0 ? '未作答' : `正确率 ${item.summary.accuracy}%`}
                    </span>
                    <span data-tone={item.summary.wrong > 0 ? 'warn' : 'muted'}>
                      错题 {item.summary.wrong}
                    </span>
                    <span data-tone={item.push_status === 'failed' ? 'bad' : 'muted'}>
                      {PUSH_LABEL[item.push_status]}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h2>
                <CalendarClock size={15} /> 定时与推送
              </h2>
            </div>
            <div className="mini-list">
              <div className="mini-item">
                <span className="k">触发时间</span>
                <span className="n">{scheduleText}</span>
              </div>
              <div className="mini-item">
                <span className="k">状态</span>
                <span className="n" data-tone={schedule?.enabled ? 'good' : 'muted'}>
                  {schedule?.enabled ? '已开启' : '已关闭'}
                </span>
              </div>
              <div className="mini-item">
                <span className="k">下次触发</span>
                <span className="n">{nextRun || (schedule?.enabled ? '待计算' : '—')}</span>
              </div>
              <div className="mini-item">
                <span className="k">邮件</span>
                <span className="n">{PUSH_LABEL[pushState.status]}</span>
              </div>
            </div>
            <div className="card-body">
              <div className="notice" style={{ marginTop: 2 }}>
                <Mail />
                <span>定时任务只在 A4S 运行时触发，关机期间的不会补发。</span>
              </div>
              <div className="rv-actions-row">
                <Button
                  size="sm"
                  variant="outline"
                  icon={<Timer size={13} />}
                  loading={busy === 'run'}
                  onClick={() => void runNow()}
                >
                  立即跑一次
                </Button>
                <Link to="/settings" className="btn btn-sm">
                  去设置
                </Link>
              </div>
            </div>
          </div>

        </div>
      </div>

      {detail && (
        <div className="card rv-detail">
          <div className="card-head">
            <h2>
              <FileText size={16} /> 最近 {detail.window_days} 天 · {fmtDay(detail.period_start)} →{' '}
              {fmtDay(detail.period_end)}
            </h2>
            <div className="rv-detail-actions">
              <span className="rv-source">{SOURCE_LABEL[detail.summary.analysis_source]}</span>
              <Button
                size="sm"
                variant="ghost"
                icon={<Send size={13} />}
                loading={busy === 'push'}
                onClick={() => void push(detail.id)}
              >
                {detail.push_status === 'failed' ? '重新推送' : '发送邮件'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<Trash2 size={13} />}
                onClick={() => setConfirmId(detail.id)}
              >
                删除
              </Button>
            </div>
          </div>
          <div className="rv-detail-body">
            <MarkdownView markdown={detail.markdown} />
          </div>
        </div>
      )}

      <Modal
        open={confirmId !== null}
        title="删除这份回顾？"
        description="删除后无法恢复。错题本与练习记录不受影响。"
        onClose={() => setConfirmId(null)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmId(null)}>
              取消
            </Button>
            <Button
              variant="danger"
              loading={busy === 'delete'}
              icon={<RefreshCw size={13} />}
              onClick={() => confirmId && void remove(confirmId)}
            >
              确认删除
            </Button>
          </>
        }
      />
    </section>
  )
}
