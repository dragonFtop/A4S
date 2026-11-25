import type { PracticeQuestion } from '../../api/client'
import { LETTERS } from './shared'

export type QuestionMode = 'review' | 'answer' | 'result'

/** 选项文字：`B. 说法二`，用于成绩页回显「你选了什么」。 */
export function formatAnswer(question: PracticeQuestion, value: string): string {
  if (!value) return '未作答'
  if (question.type === 'single_choice' || question.type === 'multi_choice') {
    return value
      .split('')
      .map((letter) => {
        const text = question.options[LETTERS.indexOf(letter)]
        return text ? `${letter}. ${text}` : letter
      })
      .join('；')
  }
  return value
}

interface Props {
  question: PracticeQuestion
  mode: QuestionMode
  /** answer 模式下的当前作答；result 模式传用户答案用于高亮 */
  value?: string
  selfGrade?: 'mastered' | 'unsure' | null
  onChange?: (value: string) => void
  onSelfGrade?: (grade: 'mastered' | 'unsure') => void
  /** review 模式下是否点亮正确项：草稿核对时「不点开答案先自己读一遍」更有用 */
  reveal?: boolean
}

/**
 * 题目正文：草稿核对、作答、成绩回顾三种模式共用一套渲染。
 *
 * 分开写三份的代价是「选项对不对」的判定会在三处各写一遍，早晚不一致；
 * 集中在这里，正确项的判定只有一处，改判分口径时不会漏掉某个视图。
 */
export default function QuestionBody({
  question,
  mode,
  value = '',
  selfGrade = null,
  onChange,
  onSelfGrade,
  reveal = true,
}: Props) {
  const interactive = mode === 'answer'
  const picked = question.type === 'multi_choice' ? value.toUpperCase() : value
  const canReveal = mode !== 'answer' && reveal

  if (question.type === 'single_choice' || question.type === 'multi_choice') {
    return (
      <div className="pq-options">
        {question.options.map((option, index) => {
          const letter = LETTERS[index] ?? ''
          const isCorrect =
            question.type === 'multi_choice'
              ? question.answer.includes(letter)
              : question.answer === letter
          const isPicked =
            mode === 'answer'
              ? picked.includes(letter)
              : Boolean(value) && value.includes(letter)
          const tone =
            mode === 'answer'
              ? isPicked
                ? 'picked'
                : undefined
              : canReveal && isCorrect
                ? 'correct'
                : isPicked
                  ? 'wrong'
                  : undefined

          return (
            <button
              type="button"
              key={letter}
              className="pq-option"
              data-tone={tone}
              data-static={!interactive}
              // 多选少选时，选中的那一项本身也是正确项，光靠颜色看不出「我选了它」
              data-picked={isPicked && isCorrect ? 'true' : undefined}
              onClick={
                interactive
                  ? () => {
                      if (question.type === 'multi_choice') {
                        const next = isPicked
                          ? picked.replace(letter, '')
                          : `${picked}${letter}`
                        onChange?.(
                          next
                            .split('')
                            .filter((item) => item && LETTERS.includes(item))
                            .sort()
                            .join('')
                        )
                      } else {
                        onChange?.(letter)
                      }
                    }
                  : undefined
              }
            >
              <span className="pq-option-key">{letter}</span>
              <span>{option}</span>
              {question.type === 'multi_choice' && interactive && (
                <span className="pq-option-box" data-on={isPicked} />
              )}
            </button>
          )
        })}
      </div>
    )
  }

  if (question.type === 'true_false') {
    return (
      <div className="pq-options pq-options-judge">
        {['正确', '错误'].map((label) => {
          const isCorrect = question.answer === label
          const isPicked = value === label
          const tone =
            mode === 'answer'
              ? isPicked
                ? 'picked'
                : undefined
              : canReveal && isCorrect
                ? 'correct'
                : isPicked
                  ? 'wrong'
                  : undefined
          return (
            <button
              type="button"
              key={label}
              className="pq-option"
              data-tone={tone}
              data-static={!interactive}
              onClick={interactive ? () => onChange?.(label) : undefined}
            >
              <span className="pq-option-key">{label === '正确' ? '✓' : '✕'}</span>
              <span>{label}</span>
            </button>
          )
        })}
      </div>
    )
  }

  // 简答题：不提前看到参考答案就没法自评，所以只在作答时展开对照。
  // 回顾态由外层「你的答案 / 正确答案」负责，这里再渲染一遍就是重复。
  if (mode !== 'answer') return null

  return (
    <div className="ans-short">
      <textarea
        className="ans-text"
        value={value}
        rows={3}
        placeholder="先写下自己的理解，再对照参考答案自评…"
        onChange={(event) => onChange?.(event.target.value)}
      />

      <div className="ans-reference">
        <div className="ans-reference-k">参考答案</div>
        <p className="ans-reference-v">{question.answer}</p>
      </div>

      <div className="ans-self">
        <span className="ans-self-label">对照之后，这题算掌握了吗？</span>
        <div className="ans-self-btns">
          <button
            type="button"
            data-on={selfGrade === 'mastered'}
            onClick={() => onSelfGrade?.('mastered')}
          >
            掌握
          </button>
          <button
            type="button"
            data-on={selfGrade === 'unsure'}
            onClick={() => onSelfGrade?.('unsure')}
          >
            未掌握
          </button>
        </div>
      </div>
    </div>
  )
}
