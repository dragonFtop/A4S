import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  ArrowUpRight,
  CalendarRange,
  Check,
  ChevronDown,
  History,
  Loader2,
  ShieldCheck,
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
  getPlan,
  listPlans,
  planStream,
  type PlanBrief,
  type PlanDetail,
  type PlanResult,
  type PlanStep,
} from '../api/client'

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
 * 学习计划（M7）。
 *
 * 与其它页面最大的不同：结果不是一次请求返回的，而是一串「动作」——
 * 查进度 → 找薄弱点 → 翻错题 → 必要时出题 → 写计划。
 * 所以这里把过程本身当成内容展示：每一步都可展开看入参与返回，
 * 这样计划里的每个数字都能对回到某次查询（验收标准见 AGENT_DECISION §3.6.4）。
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
  const [history, setHistory] = useState<PlanBrief[]>([])
  const abortRef = useRef<AbortController | null>(null)

  const loadHistory = useCallback(() => {
    listPlans(20)
      .then(setHistory)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    loadHistory()
  }, [loadHistory])

  const start = async () => {
    if (running) return
    setRunning(true)
    setThinking(null)
    setSteps([])
    setPlan(null)
    setDetail(null)
    setNotice(null)

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
          },
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
        preview: JSON.stringify(step.result ?? {}, null, 2),
        elapsed_ms: step.elapsed_ms,
      }))
    : steps

  const shown = detail ?? plan
  const activeId = detail?.id ?? plan?.plan_id ?? null

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>学习 Agent</h1>
          <p className="page-sub">观察员查数据、规划员写计划、审核员核对数字，记录员才写入：最多 6 步，每个数字都能点回出处。</p>
        </div>
        <div className="page-actions">
          {running ? (
            <Button variant="ghost" onClick={stop}>
              停止
            </Button>
          ) : (
            <Button variant="primary" icon={<Sparkles size={15} />} onClick={start}>
              让 Agent 开始
            </Button>
          )}
        </div>
      </header>

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

          <div className="agent-loop" aria-label="Agent 的工作方式">
            <span className="agent-loop-step">
              <b>1</b> 看进度与薄弱点
            </span>
            <ArrowRight size={12} className="agent-loop-arrow" />
            <span className="agent-loop-step">
              <b>2</b> 按需出一套题
            </span>
            <ArrowRight size={12} className="agent-loop-arrow" />
            <span className="agent-loop-step">
              <b>3</b> 写出 7 天计划
            </span>
            <span className="agent-loop-note">只读数据 · 唯一写操作是保存这份计划</span>
          </div>

          {timeline.length > 0 && (
            <ol className="plan-steps" aria-label="生成过程">
              {timeline.map((step) => (
                <li key={`${step.index}-${step.tool}`} className={`plan-step${step.ok ? '' : ' is-fail'}`}>
                  <span className="plan-step-dot">
                    {step.ok ? <Check size={11} /> : <X size={11} />}
                  </span>
                  <div className="plan-step-main">
                    <div className="plan-step-head">
                      {step.role_label && <span className="plan-step-role">{step.role_label}</span>}
                      <span className="plan-step-label">{step.label}</span>
                      <span className="plan-step-time">{step.elapsed_ms} ms</span>
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
                    <details className="plan-step-more" key={`detail-${step.index}-${activeId ?? 'live'}`}>
                      <summary>这一步的数据</summary>
                      <pre>
                        入参 {JSON.stringify(step.args, null, 2)}
                        {'\n'}
                        {step.preview}
                      </pre>
                    </details>
                  </div>
                </li>
              ))}
            </ol>
          )}

          {running && (
            <p className="plan-thinking">
              <Loader2 size={13} className="spin" />
              {thinking ? `正在思考第 ${thinking.step} 步（最多 ${thinking.steps_max} 步）…` : '正在收尾…'}
            </p>
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
                  {shown.steps ? ` · ${shown.steps} 步` : ''}
                  {'elapsed_seconds' in shown && shown.elapsed_seconds
                    ? ` · ${Math.round(shown.elapsed_seconds)} 秒`
                    : ''}
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
                <h3>Agent 还没开始</h3>
                <p>说一句目标，它会先看资料与错题；走到哪一步都会在上面亮出来，大约 30～90 秒。</p>
              </div>
            )
          )}
        </div>

        <div className="stack">
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
                        {when(item.created_at)} · {item.steps} 步 · {item.stats.documents} 份资料
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

          <div className="card">
            <div className="card-head">
              <h2>
                <ShieldCheck size={15} /> Agent 边界
              </h2>
            </div>
            <ul className="plan-scope">
              <li>只读数据、只写这份计划</li>
              <li>每个数字都来自上面某一步，不自己算</li>
              <li>不判分、不排复习日期</li>
              <li>一次一份 7 天计划，数据为空不出</li>
            </ul>
            <details className="plan-scope-more">
              <summary>
                它调用的工具 <ChevronDown size={12} />
              </summary>
              <ul>
                <li>
                  <code>get_progress</code> 进度统计
                </li>
                <li>
                  <code>list_weak_points</code> 薄弱知识点
                </li>
                <li>
                  <code>list_wrong_answers</code> 错题明细
                </li>
                <li>
                  <code>generate_practice</code> 出一套题
                </li>
                <li>
                  <code>save_plan</code> 落库（唯一写操作）
                </li>
              </ul>
            </details>
          </div>
        </div>
      </div>
    </section>
  )
}
