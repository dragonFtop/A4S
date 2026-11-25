import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, Send, Timer } from 'lucide-react'
import Button from '../ui/Button'
import Modal from '../ui/Modal'
import { useToast } from '../ui/Toast'
import {
  savePracticeAnswer,
  submitPractice,
  type AnswerInput,
  type PracticeQuestion,
  type PracticeSession,
} from '../../api/client'
import QuestionBody from './QuestionBody'
import { errText, pad2, typeLabel } from './shared'

interface Props {
  session: PracticeSession
  onSubmitted: (session: PracticeSession) => void
  onExit: () => void
}

const SAVE_DELAY = 600

/**
 * 答题卡。
 *
 * 两个决定值得说明：
 * 1. 每次点选都落库（600ms 防抖）。断点续答只能靠持久化，不能靠内存或 localStorage——
 *    换个浏览器、换台设备就该能接着答。
 * 2. 简答必须自评才算已答。没有自评就没有判分依据，宁可提示「没答完」，
 *    也不替用户猜一个「掌握」。
 */
export default function AnswerSheet({ session, onSubmitted, onExit }: Props) {
  const toast = useToast()
  const [values, setValues] = useState<Record<string, string>>({})
  const [grades, setGrades] = useState<Record<string, 'mastered' | 'unsure'>>({})
  const [submitting, setSubmitting] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  // 事件处理里读 state 会拿到同一个 tick 的旧值，写入对与发送队列都以 ref 为准
  const valuesRef = useRef<Record<string, string>>({})
  const gradesRef = useRef<Record<string, 'mastered' | 'unsure'>>({})
  const queue = useRef(new Map<string, AnswerInput>())
  const timer = useRef<number | null>(null)
  const firstTouch = useRef<Record<string, number>>({})

  // 回填：后端存的是归一化后的答案，简答的自评只能由 is_correct 反推
  useEffect(() => {
    const nextValues: Record<string, string> = {}
    const nextGrades: Record<string, 'mastered' | 'unsure'> = {}
    const questionMap = new Map(session.questions.map((question) => [question.id, question]))
    session.answers.forEach((answer) => {
      nextValues[answer.question_id] = answer.response
      if (questionMap.get(answer.question_id)?.type === 'short_answer') {
        nextGrades[answer.question_id] = answer.is_correct ? 'mastered' : 'unsure'
      }
    })
    valuesRef.current = nextValues
    gradesRef.current = nextGrades
    setValues(nextValues)
    setGrades(nextGrades)
  }, [session.id, session.answers, session.questions])

  const spent = (questionId: string): number => {
    const started = firstTouch.current[questionId]
    return started ? (Date.now() - started) / 1000 : 0
  }

  const flush = useCallback(async () => {
    if (timer.current) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    const items = Array.from(queue.current.values())
    queue.current.clear()
    if (!items.length) return
    for (const item of items) {
      try {
        await savePracticeAnswer(session.id, item)
      } catch (error) {
        toast.error(errText(error))
        return
      }
    }
    setSavedAt(Date.now())
  }, [session.id, toast])

  // 离开页面时补一次保存：防抖窗口内关掉页签不该丢掉最后一下点击
  useEffect(() => {
    return () => {
      const items = Array.from(queue.current.values())
      queue.current.clear()
      items.forEach((item) => {
        void savePracticeAnswer(session.id, item).catch(() => undefined)
      })
    }
  }, [session.id])

  const setAnswer = (
    question: PracticeQuestion,
    value: string,
    nextGrade?: 'mastered' | 'unsure'
  ) => {
    if (!firstTouch.current[question.id]) firstTouch.current[question.id] = Date.now()

    const nextValues = { ...valuesRef.current, [question.id]: value }
    valuesRef.current = nextValues
    setValues(nextValues)

    let nextGrades = gradesRef.current
    if (nextGrade) {
      nextGrades = { ...nextGrades, [question.id]: nextGrade }
      gradesRef.current = nextGrades
      setGrades(nextGrades)
    }

    queue.current.set(question.id, {
      question_id: question.id,
      response: value,
      duration_seconds: Math.round(spent(question.id)),
      self_grade: nextGrades[question.id] ?? null,
    })
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      void flush()
    }, SAVE_DELAY)
  }

  const isAnswered = (question: PracticeQuestion): boolean => {
    if (question.type === 'short_answer') return Boolean(grades[question.id])
    return (values[question.id] ?? '').trim() !== ''
  }

  const gradeOf = (questionId: string): 'mastered' | 'unsure' | null =>
    gradesRef.current[questionId] ?? null

  const answeredCount = useMemo(
    () => session.questions.filter(isAnswered).length,
    // isAnswered 依赖 values/grades，两个 state 变化时必须重算
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [session.questions, values, grades]
  )
  const total = session.questions.length
  const percent = total ? Math.round((answeredCount / total) * 100) : 0
  const missing = total - answeredCount

  const doSubmit = async () => {
    setSubmitting(true)
    setConfirmOpen(false)
    try {
      await flush()
      const payload: AnswerInput[] = session.questions.filter(isAnswered).map((question) => ({
        question_id: question.id,
        response: valuesRef.current[question.id] ?? '',
        self_grade: gradeOf(question.id),
        duration_seconds: Math.round(spent(question.id)),
      }))
      const done = await submitPractice(session.id, payload)
      onSubmitted(done)
    } catch (error) {
      toast.error(errText(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="ans-wrap">
      <div className="ans-bar">
        <div className="ans-bar-main">
          <span className="ans-bar-count">
            {answeredCount}
            <em>/{total}</em>
          </span>
          <div className="ans-track">
            <div className="ans-fill" style={{ width: `${percent}%` }} />
          </div>
          <span className="ans-bar-hint">
            {savedAt ? (
              <>
                <CheckCircle2 size={11} /> 已自动保存
              </>
            ) : (
              <>
                <Timer size={11} /> 答过的题随时保存
              </>
            )}
          </span>
        </div>
        <div className="ans-bar-actions">
          <Button size="sm" variant="ghost" onClick={onExit}>
            先退出
          </Button>
          <Button
            size="sm"
            variant="primary"
            icon={<Send size={13} />}
            loading={submitting}
            onClick={() => (missing > 0 ? setConfirmOpen(true) : void doSubmit())}
          >
            提交判分
          </Button>
        </div>
      </div>

      <div className="ans-list">
        {session.questions.map((question, index) => (
          <article className="ans-card" key={question.id} id={`practice-q${index + 1}`}>
            <div className="pq-head">
              <span className="pq-idx">{pad2(index + 1)}</span>
              <span className="tag">{typeLabel(question.type)}</span>
              {question.knowledge_point && <span className="pq-kp">{question.knowledge_point}</span>}
              {isAnswered(question) && <span className="ans-done">已答</span>}
            </div>
            <p className="pq-stem">{question.stem}</p>
            <QuestionBody
              question={question}
              mode="answer"
              value={values[question.id] ?? ''}
              selfGrade={grades[question.id] ?? null}
              onChange={(value) => setAnswer(question, value)}
              onSelfGrade={(grade) =>
                setAnswer(question, valuesRef.current[question.id] ?? '', grade)
              }
            />
          </article>
        ))}
      </div>

      <Modal
        open={confirmOpen}
        title={`还有 ${missing} 题没有作答`}
        description="未作答的题按 0 分计，但不会进入错题本——没做过不等于做错。确定现在提交吗？"
        onClose={() => setConfirmOpen(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              回去补答
            </Button>
            <Button variant="primary" onClick={() => void doSubmit()} loading={submitting}>
              仍然提交
            </Button>
          </>
        }
      />
    </div>
  )
}
