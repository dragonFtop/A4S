import { useCallback, useEffect, useState } from 'react'
import { Activity, RefreshCw } from 'lucide-react'
import {
  getLatestKbHealth,
  runKbHealth,
  type HealthReport,
  type HealthSeverity,
} from '../../api/client'
import Button from '../ui/Button'
import { useToast } from '../ui/Toast'
import { formatDateTime } from '../../utils/format'

const SEVERITY_LABEL: Record<HealthSeverity, string> = {
  high: '待修复',
  medium: '建议',
  low: '提醒',
}

/** 知识库体检条：只读检查资料片段、解析状态、重复与主题覆盖。 */
export default function HealthPanel() {
  const toast = useToast()
  const [report, setReport] = useState<HealthReport | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    getLatestKbHealth()
      .then(setReport)
      .catch(() => setReport(null))
  }, [])

  const run = useCallback(async () => {
    setLoading(true)
    try {
      const next = await runKbHealth()
      setReport(next)
      if (next.findings.length === 0) toast.success('体检通过：没有发现问题')
      else toast.info(`体检完成：${next.findings.length} 个问题待处理`)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [toast])

  const findings = report?.findings ?? []
  const fixes = new Map((report?.proposals ?? []).map((item) => [item.finding_id, item.label]))
  const shown = findings.slice(0, 6)

  return (
    <section className="kb-health" aria-label="知识库体检">
      <div className="kb-health-head">
        <div className="kb-health-title">
          <Activity size={15} />
          <strong>知识库体检</strong>
          {report && (
            <span
              className={`kb-health-score${
                report.score < 60 ? ' is-bad' : report.score < 90 ? ' is-warn' : ''
              }`}
            >
              {report.score} 分
            </span>
          )}
          <span className="kb-health-note">
            {report
              ? `${report.documents} 份资料 · ${findings.length} 个问题`
              : '还没体检过，体检只读资料，不改动内容'}
          </span>
        </div>
        <div className="kb-health-actions">
          {report && (
            <span className="kb-health-time">
              上次 {formatDateTime(report.created_at)} · {report.elapsed_ms} ms
            </span>
          )}
          <Button
            size="sm"
            variant="ghost"
            loading={loading}
            icon={<RefreshCw size={13} />}
            onClick={run}
          >
            {report ? '再体检一次' : '体检一次'}
          </Button>
        </div>
      </div>

      {shown.length > 0 && (
        <ul className="kb-health-list">
          {shown.map((item) => (
            <li key={item.id} className={`kb-health-item sev-${item.severity}`}>
              <span className="kb-health-tag">{SEVERITY_LABEL[item.severity] ?? item.severity}</span>
              <span className="kb-health-claim">{item.claim}</span>
              {fixes.get(item.id) && <span className="kb-health-fix">{fixes.get(item.id)}</span>}
            </li>
          ))}
          {findings.length > shown.length && (
            <li className="kb-health-more">还有 {findings.length - shown.length} 条，修完再体检一次</li>
          )}
        </ul>
      )}
    </section>
  )
}