import { useCallback, useEffect, useState } from 'react'
import { Check, Handshake, Send, ShieldCheck, Users, X } from 'lucide-react'
import { ApiError, shareApi, type ShareInfo } from '../api/client'
import { useSpace } from '../context/SpaceContext'
import { useToast } from '../components/ui/Toast'
import Empty, { Spinner } from '../components/ui/Feedback'

const STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  active: '已生效',
  rejected: '已拒绝',
  revoked: '已撤销',
}

function statusText(status: string): string {
  return STATUS_LABEL[status] ?? status
}

function isPending(share: ShareInfo): boolean {
  return share.status === 'pending'
}

function isActive(share: ShareInfo): boolean {
  return share.status === 'active'
}

export default function Shares() {
  const { spaces, current } = useSpace()
  const toast = useToast()
  const [granted, setGranted] = useState<ShareInfo[]>([])
  const [incoming, setIncoming] = useState<ShareInfo[]>([])
  const [outgoing, setOutgoing] = useState<ShareInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [applyName, setApplyName] = useState('')
  const [grantSpace, setGrantSpace] = useState('')
  const [grantName, setGrantName] = useState('')

  const ownedGroups = spaces.filter((s) => s.kind === 'group' && s.my_role === 'owner')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const board = await shareApi.board()
      setGranted(board.granted)
      setIncoming(board.incoming)
      setOutgoing(board.outgoing)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '读取共享失败')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!grantSpace && ownedGroups.length > 0) setGrantSpace(ownedGroups[0].id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownedGroups.length])

  async function decide(share: ShareInfo, action: 'approve' | 'reject') {
    if (busy) return
    setBusy(true)
    try {
      await shareApi.decide(share.space_id, share.id, action)
      toast.success(action === 'approve' ? '已批准共享' : '已拒绝共享')
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  async function revoke(share: ShareInfo) {
    if (busy) return
    setBusy(true)
    try {
      await shareApi.revoke(share.space_id, share.id)
      toast.success('已撤销共享')
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '撤销失败')
    } finally {
      setBusy(false)
    }
  }

  async function cancel(share: ShareInfo) {
    if (busy) return
    setBusy(true)
    try {
      await shareApi.cancel(share.id)
      toast.success('已撤回申请')
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '撤回失败')
    } finally {
      setBusy(false)
    }
  }

  async function submitApply(e: React.FormEvent) {
    e.preventDefault()
    const name = applyName.trim()
    if (!name || busy) return
    setBusy(true)
    try {
      await shareApi.apply(name)
      setApplyName('')
      toast.success('已提交共享申请，等对方批准')
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '申请失败')
    } finally {
      setBusy(false)
    }
  }

  async function submitGrant(e: React.FormEvent) {
    e.preventDefault()
    const name = grantName.trim()
    if (!name || !grantSpace || busy) return
    setBusy(true)
    try {
      await shareApi.grant(grantSpace, name)
      setGrantName('')
      toast.success('已主动开放给该用户')
      await load()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : '开放失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>共享管理</h1>
          <p className="page-sub">共享只读，撤销即失效。</p>
        </div>
      </header>

      {loading ? (
        <Spinner text="正在读取共享…" />
      ) : (
        <div className="space-grid">
          <section className="card">
            <div className="card-head">
              <h2>
                <ShieldCheck size={16} /> 我开放的
              </h2>
            </div>
            <div className="card-body">
            {granted.length === 0 ? (
              <Empty title="还没有共享" description="批准别人的申请，或在下面主动开放某个小组。" />
            ) : (
              <ul className="member-list">
                {granted.map((s) => (
                  <li key={s.id} className="member-row">
                    <span className="avatar">
                      {(s.grantee_name || s.grantee_username || '?').slice(0, 1)}
                    </span>
                    <span className="member-main">
                      <span className="member-name">
                        {s.space_name || '小组'} → {s.grantee_name || s.grantee_username}
                      </span>
                      <span className="member-sub">
                        {statusText(s.status)}
                        {s.message ? ` · ${s.message}` : ''}
                      </span>
                    </span>
                    <span className="member-actions">
                      {isPending(s) && (
                        <>
                          <button
                            type="button"
                            className="icon-btn"
                            title="批准"
                            disabled={busy}
                            onClick={() => void decide(s, 'approve')}
                          >
                            <Check size={15} />
                          </button>
                          <button
                            type="button"
                            className="icon-btn danger"
                            title="拒绝"
                            disabled={busy}
                            onClick={() => void decide(s, 'reject')}
                          >
                            <X size={15} />
                          </button>
                        </>
                      )}
                      {isActive(s) && (
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs"
                          disabled={busy}
                          onClick={() => void revoke(s)}
                        >
                          撤销
                        </button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {ownedGroups.length > 0 ? (
              <form className="inline-form" onSubmit={submitGrant}>
                <select
                  className="select"
                  value={grantSpace}
                  onChange={(e) => setGrantSpace(e.target.value)}
                >
                  {ownedGroups.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
                <input
                  className="input"
                  value={grantName}
                  onChange={(e) => setGrantName(e.target.value)}
                  placeholder="对方的用户名"
                />
                <button className="btn" type="submit" disabled={busy || !grantName.trim()}>
                  <Users size={15} />
                  <span>主动开放</span>
                </button>
              </form>
            ) : (
              <p className="hint">你还没有可开放的小组（先创建学习小组）。</p>
            )}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>
                <Handshake size={16} /> 共享给我的
              </h2>
            </div>
            <div className="card-body">
            {incoming.length === 0 ? (
              <Empty title="暂无" description="别人把知识库共享给你后，会出现在这里。" />
            ) : (
              <ul className="member-list">
                {incoming.map((s) => (
                  <li key={s.id} className="member-row">
                    <span className="avatar">{(s.owner_name || '?').slice(0, 1)}</span>
                    <span className="member-main">
                      <span className="member-name">{s.space_name || '知识库'}</span>
                      <span className="member-sub">
                        来自 {s.owner_name || '对方'} · {statusText(s.status)} · 只读
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <form className="inline-form" onSubmit={submitApply}>
              <input
                className="input"
                value={applyName}
                onChange={(e) => setApplyName(e.target.value)}
                placeholder="向某位用户申请共享（填用户名）"
              />
              <button className="btn" type="submit" disabled={busy || !applyName.trim()}>
                <Send size={15} />
                <span>申请</span>
              </button>
            </form>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>
                <Send size={16} /> 我申请的
              </h2>
            </div>
            <div className="card-body">
            {outgoing.length === 0 ? (
              <Empty title="暂无" description="你发起的共享申请会显示在这里。" />
            ) : (
              <ul className="member-list">
                {outgoing.map((s) => (
                  <li key={s.id} className="member-row">
                    <span className="avatar">{(s.owner_name || '?').slice(0, 1)}</span>
                    <span className="member-main">
                      <span className="member-name">{s.space_name || '知识库'}</span>
                      <span className="member-sub">
                        对方 {s.owner_name || ''} · {statusText(s.status)}
                      </span>
                    </span>
                    {isPending(s) && (
                      <span className="member-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs"
                          disabled={busy}
                          onClick={() => void cancel(s)}
                        >
                          撤回
                        </button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {current?.my_role === 'viewer' && (
              <p className="hint">当前空间是只读共享：可以检索与问答，不能写入。</p>
            )}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
