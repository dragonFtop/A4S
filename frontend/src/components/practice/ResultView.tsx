import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, ChevronDown, ListChecks, NotebookPen, RotateCcw } from 'lucide-react'
import Button from '../ui/Button'
import type { PracticeSession } from '../../api/client'
import QuestionBody, { formatAnswer } from './QuestionBody'
import { formatClock, formatDuration, pad2, typeLabel } from './shared'

interface Props {
  session: PracticeSession
  onAgain: () => void
}

function verdict(score: number, answered: number, total: number): { title: string; note: string } {
  if (answered === 0) {
    return {
      title: '没有作答记录',
      note: '这套题一道都没有答。重新进入作答，或者直接换一套。',
    }
  }
  if (score >= 90) {
    return { title: '这个主题基本清楚了', note: '错的那一两道值得看一眼，多半是边界条件。' }
  }
  if (score >= 70) {
    return { title: '大方向对，细节有漏', note: '点开错题的原文片段，漏的通常在原文的某一句里。' }
  }
  if (score >= 50) {
    return { title: '对了一半', note: '先回去重读错题指向的段落，再重练这一批错题。' }
  }
  return {
    title: '这一块还没立住',
    note: `${total} 题对了 ${Math.round((score / 100) * total)} 题。与其反复刷题，不如先把资料读一遍。`,
  }
}

/** 成绩页：分数、指标、逐题回顾（错题与未答默认展开，答对的收起）。 */
export default function ResultView({ session, onAgain }: Props) {
  const navigate = useNavigate()
  const stats = session.stats
  const answerMap = useMemo(
    () => new Map(session.answers.map((answer) => [answer.question_id, answer])),
    [session.answers]
  )
  const [openIds, setOpenIds] = useState<string[]>(() =>
    session.questions
      .filter((question) => !answerMap.get(question.id)?.is_correct)
      .map((question) => question.id)
  )

  const total = session.questions.length || stats?.total || 0
  const correct = stats?.correct ?? session.answers.filter((item) => item.is_correct).length
  const answered = stats?.answered ?? session.answers.length
  const accuracy = stats?.accuracy ?? session.score
  const wrongCount = session.answers.filter((item) => !item.is_correct).length
  const text = verdict(accuracy, answered, total)
  const tone = accuracy >= 80 ? 'good' : accuracy >= 60 ? 'mid' : 'low'

  const toggle = (questionId: string) =>
    setOpenIds((prev) =>
      prev.includes(questionId) ? prev.filter((item) => item !== questionId) : [...prev, questionId]
    )

  return (
    <div className="res-wrap">
      <div className="res-head">
        <div className="score-ring" data-tone={tone} style={{ ['--pct' as string]: `${accuracy}%` }}>
          <span className="score-num">{Math.round(accuracy)}</span>
          <span className="score-unit">分</span>
        </div>
        <div className="res-head-main">
          <h2 className="res-title">{text.title}</h2>
          <p className="res-note">{text.note}</p>
          <div className="res-metrics">
            <div className="res-metric">
              <span className="res-metric-n">
                {correct}
                <em>/{total}</em>
              </span>
              <span className="res-metric-k">答对</span>
            </div>
            <div className="res-metric">
              <span className="res-metric-n">{answered}</span>
              <span className="res-metric-k">已作答</span>
            </div>
            <div className="res-metric">
              <span className="res-metric-n">{wrongCount}</span>
              <span className="res-metric-k">进错题本</span>
            </div>
            <div className="res-metric">
              <span className="res-metric-n">{formatDuration(stats?.duration_seconds ?? 0)}</span>
              <span className="res-metric-k">用时</span>
            </div>
          </div>
        </div>
        <div className="res-actions">
          <Button size="sm" variant="ghost" icon={<RotateCcw size={13} />} onClick={onAgain}>
            再练一套
          </Button>
          <Button
            size="sm"
            variant="outline"
            icon={<NotebookPen size={13} />}
            onClick={() => navigate('/wrong')}
          >
            打开错题本
          </Button>
        </div>
      </div>

      <div className="res-list">
        {session.questions.map((question, index) => {
          const answer = answerMap.get(question.id)
          const state = !answer ? 'skipped' : answer.is_correct ? 'right' : 'wrong'
          const open = openIds.includes(question.id)
          return (
            <article className="res-item" key={question.id} data-state={state}>
              <button type="button" className="res-item-head" onClick={() => toggle(question.id)}>
                <span className="pq-idx">{pad2(index + 1)}</span>
                <span className="tag">{typeLabel(question.type)}</span>
                <span className="res-state">
                  {state === 'right' ? '答对' : state === 'wrong' ? '答错' : '未作答'}
                </span>
                <span className="res-item-stem">{question.stem}</span>
                <ChevronDown
                  size={14}
                  className="res-chevron"
                  style={{ transform: open ? 'rotate(180deg)' : undefined }}
                />
              </button>

              {open && (
                <div className="res-item-body">
                  <div className="res-compare">
                    <div className="res-pair" data-kind="yours">
                      <span className="res-pair-k">你的答案</span>
                      <span className="res-pair-v">
                        {formatAnswer(question, answer?.response ?? '')}
                      </span>
                    </div>
                    <ArrowRight size={13} className="res-pair-arrow" />
                    <div className="res-pair" data-kind="right">
                      <span className="res-pair-k">正确答案</span>
                      <span className="res-pair-v">{formatAnswer(question, question.answer)}</span>
                    </div>
                  </div>

                  <QuestionBody
                    question={question}
                    mode="result"
                    value={answer?.response ?? ''}
                  />

                  <div className="pq-answer">
                    <div className="pq-answer-line">
                      <span className="pq-answer-k">解析</span>
                      <span className="pq-answer-v">{question.explanation}</span>
                    </div>
                  </div>

                  {question.sources.length > 0 && (
                    <div className="pq-sources">
                      {question.sources.map((source) => (
                        <div key={`${question.id}-${source.chunk_id}`} className="pq-snippet">
                          <span className="pq-snippet-src">
                            《{source.document_name}》
                            {source.page ? ` 第 ${source.page} 页` : ''}
                          </span>
                          {source.snippet}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </article>
          )
        })}
      </div>

      <div className="res-foot">
        <span className="muted" style={{ fontSize: 11 }}>
          <ListChecks size={12} /> 完成于 {formatClock(session.finished_at)} · 错题已收进错题本
        </span>
        <Button
          variant="primary"
          icon={<NotebookPen size={14} />}
          onClick={() => navigate('/wrong')}
        >
          去处理错题
        </Button>
      </div>
    </div>
  )
}
