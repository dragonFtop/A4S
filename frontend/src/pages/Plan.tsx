import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowUpRight,
  CalendarRange,
  Check,
  History,
  Info,
  Loader2,
  Sparkles,
  Target,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'
import Button from '../components/ui/Button'
import { useToast } from '../components/ui/Toast'
import MarkdownView from '../components/notes/MarkdownView'
import {
  deletePlan,
  getOverview,
  getPlan,
  listPlans,
  planStream,
  type OverviewData,
  type PlanBrief,
  type PlanDetail,
  type PlanResult,
  type PlanStep,
} from '../api/client'
import { formatScore } from '../utils/format'

type Notice = { kind: 'no_data' | 'no_plan' | 'error'; detail: string }

const NOTICE_TITLE: Record<Notice['kind'], string> = {
  no_data: '还没有可以排计划的数据',
  no_plan: '这次没排出计划',
  error: '生成中断',
}

function when(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * 计划助理（M7）。
 *
 * 页面只保留三类用户真正关心的东西：自己的学习数据、助理的执行过程、最后那份计划。
 * 观察 / 规划 / 审核 / 写入的角色交接放在右栏，按「谁做了什么」平铺，
 * 不再展开原始工具入参与返回值。
 */
export default function Plan() {
  const toast = useToast()
  const navigate = useNavigate()
  const [goal, setGoal] = useState('')
  const [running, setRunning] = useState(false)
  const [thinking, setThinking] = useState<{ step: number; steps_max: number } | null>(null)
  const [steps, setSteps] = useState<PlanStep[]>([])
  const [plan, setPlan] = useState<PlanResult | null>(null)
  const [detail, setDetail] = useState<PlanDetail | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [hint, setHint] = useState('')
  const [history, setHistory] = useState<PlanBrief[]>([])
  const [overview, setOverview] = useState<OverviewData | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadHistory = useCallback(() => {
    listPlans(20)
      .then(setHistory)
      .catch(() => undefined)
  }, [])

  const loadStats = useCallback(() => {
    getOverview()
      .then(setOverview)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    loadHistory()
    loadStats()
  }, [loadHistory, loadStats])

  const start = async () => {
    if (running) return
    setRunning(true)
    setThinking(null)
    setSteps([])
    setPlan(null)
    setDetail(null)
    setNotice(null)
    setHint('')

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await planStream(
        { goal: goal.trim() },
        {
          onThinking: setThinking,
          onStep: (step) => {
            setThinking(null)
            setSteps((prev) => [...prev, step])
          },
          onPlan: (result) => {
            setThinking(null)
            setPlan(result)
            loadHistory()
            loadStats()
          },
          onNotice: setHint,
          onNoData: (text) => setNotice({ kind: 'no_data', detail: text }),
          onNoPlan: (info) => setNotice({ kind: 'no_plan', detail: info.detail }),
          onError: (text) => setNotice({ kind: 'error', detail: text }),
        },
        controller.signal,
      )
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setNotice({ kind: 'error', detail: (err as Error).message })
      }
    } finally {
      setRunning(false)
      setThinking(null)
      abortRef.current = null
    }
  }

  const stop = () => abortRef.current?.abort()

  const openPlan = async (id: string) => {
    try {
      const data = await getPlan(id)
      setDetail(data)
      setPlan(null)
      setSteps([])
      setNotice(null)
      setHint('')
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  const removePlan = async (id: string, event: React.MouseEvent) => {
    event.stopPropagation()
    try {
      await deletePlan(id)
      if (detail?.id === id) setDetail(null)
      loadHistory()
      toast.success('已删除')
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  // 历史详情里的过程记录与实时步骤结构不同，这里抹平成一套渲染
  const timeline: PlanStep[] = detail
    ? (detail.meta.steps ?? []).map((step, index) => ({
        index: index + 1,
        role: step.role,
        role_label: step.role_label,
        tool: step.tool,
        label: step.label,
        args: step.args ?? {},
        ok: step.ok,
        brief: step.brief ?? '',
        preview: '',
        elapsed_ms: step.elapsed_ms,
      }))
    : steps

  const shown = detail ?? plan
  const activeId = detail?.id ?? plan?.plan_id ?? null

  // 审核与写入只留在运行账本里；页面上用一行摘要补齐后三位角色的交接
  const verdict = detail?.meta.agents?.verdict
  const passed = verdict ? verdict.passed !== false : true
  const handoffs: PlanStep[] = shown
    ? [
        {
          index: timeline.length + 1,
          role: 'planner',
          role_label: '规划员',
          tool: 'propose_plan',
          label: '撰写计划',
          args: {},
          ok: true,
          brief: `起草 ${shown.markdown.length} 字，提交审核`,
          preview: '',
          elapsed_ms: 0,
        },
        {
          index: timeline.length + 2,
          role: 'verifier',
          role_label: '审核员',
          tool: 'verdict',
          label: '审核计划',
          args: {},
          ok: passed,
          brief: passed
            ? verdict?.warnings?.length
              ? `审核通过，${verdict.warnings.length} 条提示`
              : '审核通过，内容与依据对得上'
            : '审核未通过',
          preview: '',
          elapsed_ms: 0,
        },
        {
          index: timeline.length + 3,
          role: 'writer',
          role_label: '记录员',
          tool: 'save_plan',
          label: '写入计划',
          args: {},
          ok: true,
          brief: '已保存到学习计划',
          preview: '',
          elapsed_ms: 0,
        },
      ]
    : []
  const fullTimeline = [...timeline, ...handoffs]

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>计划助理</h1>
          <p className="page-sub">说一句这周想学什么，助理会先看资料和错题，再给出一份 7 天计划。</p>
        </div>
        <div className="page-actions">
          {running ? (
            <Button variant="ghost" onClick={stop}>
              停止
            </Button>
          ) : (
            <Button variant="primary" icon={<Sparkles size={15} />} onClick={start}>
              开始制定计划
            </Button>
          )}
        </div>
      </header>

      <div className="plan-stats" aria-label="学习数据">
        <div className="plan-stat">
          <span className="plan-stat-value">
            {overview ? overview.documents.total : '—'}
            <i>份</i>
          </span>
          <span className="plan-stat-label">资料</span>
        </div>
        <div className="plan-stat">
          <span className="plan-stat-value">
            {overview ? overview.documents.chunks : '—'}
            <i>个</i>
          </span>
          <span className="plan-stat-label">知识片段</span>
        </div>
        <div className="plan-stat">
          <span className="plan-stat-value">
            {overview && overview.practice.finished > 0 ? formatScore(overview.practice.avg_score) : '—'}
          </span>
          <span className="plan-stat-label">练习正确率</span>
        </div>
        <div className={`plan-stat${overview && overview.wrong.total > 0 ? ' is-danger' : ''}`}>
          <span className="plan-stat-value">
            {overview ? overview.wrong.total : '—'}
            <i>道</i>
          </span>
          <span className="plan-stat-label">待复习错题</span>
        </div>
      </div>

      <div className="workbench">
        <div className="card plan-panel">
          <div className="plan-goal">
            <Target size={15} />
            <input
              className="input"
              value={goal}
              maxLength={200}
              placeholder="补充目标（可选）：这周想搞明白什么？"
              onChange={(event) => setGoal(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') start()
              }}
              disabled={running}
            />
          </div>

          {hint && (
            <div className="chat-kb plan-hint">
              <Info size={15} />
              <div className="chat-kb-body">
                <p className="chat-kb-title">当前知识库中还没有相关资料</p>
                <p className="chat-kb-detail">{hint}</p>
              </div>
              <Button size="sm" variant="ghost" onClick={() => navigate('/library')}>
                去导入资料
              </Button>
            </div>
          )}

          {notice && (
            <div className={`chat-empty plan-notice is-${notice.kind === 'no_data' ? 'empty' : 'no_match'}`}>
              <p className="chat-empty-title">
                {notice.kind === 'error' ? <TriangleAlert size={14} /> : <CalendarRange size={14} />}
                {NOTICE_TITLE[notice.kind]}
              </p>
              <p className="chat-empty-detail">{notice.detail}</p>
              {notice.kind === 'no_data' && (
                <Button size="sm" variant="ghost" onClick={() => navigate('/library')}>
                  去导入资料
                </Button>
              )}
              {notice.kind === 'no_plan' && (
                <Button size="sm" variant="ghost" onClick={start}>
                  再试一次
                </Button>
              )}
            </div>
          )}

          {shown ? (
            <div className="plan-body">
              <div className="plan-body-head">
                <span className="plan-body-meta">
                  {detail ? when(detail.created_at) : '刚刚生成'}
                </span>
                {detail && (
                  <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={(e) => removePlan(detail.id, e)}>
                    删除
                  </Button>
                )}
              </div>
              <MarkdownView markdown={shown.markdown} sources={[]} />
            </div>
          ) : (
            !running &&
            !notice && (
              <div className="thread-empty plan-empty">
                <span className="orb">
                  <Sparkles size={19} />
                </span>
                <h3>助理还没开始</h3>
                <p>写一句这周想学什么（也可以不写），助理会先看资料和错题，再给你一份 7 天计划。</p>
              </div>
            )
          )}
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h2>
                <Sparkles size={15} /> 执行过程
              </h2>
            </div>
            {fullTimeline.length > 0 ? (
              <ol className="plan-steps" aria-label="助理的执行过程">
                {fullTimeline.map((step) => (
                  <li key={`${step.index}-${step.tool}`} className={`plan-step${step.ok ? '' : ' is-fail'}`}>
                    <span className="plan-step-dot">
                      {step.ok ? <Check size={11} /> : <X size={11} />}
                    </span>
                    <div className="plan-step-main">
                      <div className="plan-step-head">
                        {step.role_label && <span className="plan-step-role">{step.role_label}</span>}
                        <span className="plan-step-label">{step.label}</span>
                      </div>
                      {step.brief && <p className="plan-step-brief">{step.brief}</p>}
                      {step.session_id && (
                        <button
                          className="plan-step-go"
                          onClick={() => navigate(`/practice?session=${step.session_id}`)}
                        >
                          去作答这套题 <ArrowUpRight size={11} />
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="plan-side-empty">
                {running ? '助理正在准备第一步…' : '开始后，助理的每一步都会显示在这里。'}
              </p>
            )}
            {running && (
              <p className="plan-thinking">
                <Loader2 size={13} className="spin" />
                {thinking ? '助理正在整理资料与错题…' : '正在收尾…'}
              </p>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <h2>
                <History size={15} /> 历史计划
              </h2>
            </div>
            {history.length === 0 ? (
              <p className="plan-side-empty">生成过的计划会留在这里。</p>
            ) : (
              <div className="plan-history">
                {history.map((item) => (
                  <div
                    key={item.id}
                    className={`plan-history-item${activeId === item.id ? ' is-active' : ''}`}
                    onClick={() => openPlan(item.id)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') openPlan(item.id)
                    }}
                  >
                    <div className="plan-history-main">
                      <span className="plan-history-title">{item.goal || item.excerpt}</span>
                      <span className="plan-history-meta">
                        {when(item.created_at)} · {item.stats.documents} 份资料
                      </span>
                    </div>
                    <button
                      className="icon-mini"
                      aria-label="删除这份计划"
                      onClick={(event) => removePlan(item.id, event)}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}