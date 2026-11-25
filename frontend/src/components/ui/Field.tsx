import type { ReactNode } from 'react'
import HelpTip from './HelpTip'

interface FieldProps {
  label?: string
  /** 常显的一行短提示，只在真的需要一直看见时才用 */
  hint?: string
  /** 收进问号的解释，点开才展开 */
  tip?: ReactNode
  error?: string
  required?: boolean
  htmlFor?: string
  children: ReactNode
  className?: string
}

/** 表单字段容器：统一标签、提示与错误的排版。 */
export default function Field({
  label,
  hint,
  tip,
  error,
  required,
  htmlFor,
  children,
  className = '',
}: FieldProps) {
  return (
    <div className={`field ${className}`}>
      {label && (
        <span className="field-label-row">
          <label className="field-label" htmlFor={htmlFor}>
            {label}
            {required && <span className="req">*</span>}
          </label>
          {tip ? <HelpTip label={`${label}的说明`}>{tip}</HelpTip> : null}
        </span>
      )}
      {children}
      {error ? (
        <span className="field-error">{error}</span>
      ) : hint ? (
        <span className="field-hint">{hint}</span>
      ) : null}
    </div>
  )
}

interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  label: string
}

export function Switch({ checked, onChange, disabled, label }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="switch"
      data-on={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  )
}
