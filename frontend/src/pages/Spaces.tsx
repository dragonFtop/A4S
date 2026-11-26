import { useCallback, useEffect, useState } from 'react'
import { Check, Crown, Plus, Trash2, UserPlus, Users } from 'lucide-react'
import { ApiError, spaceApi, type MemberInfo, type SpaceInfo } from '../api/client'
import { useSpace } from '../context/SpaceContext'
import { useToast } from '../components/ui/Toast'
import Empty, { Spinner } from '../components/ui/Feedback'

export default function Spaces() {
  const { spaces, current, switchTo, refresh, user } = useSpace()
  const toast = useToast()
  const [members, setMembers] = useState<MemberInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const [invite, setInvite] = useState('')
  const [busy, setBusy] = useState(false)

  const isOwner = current?.my_role === 'owner'
  const isPersonal = current?.kind === 'personal'

  const loadMembers = useCallback(async () => {
    if (!current) return
    setLoading(true)
    try {
      setMembers(await spaceApi.members(current.id))
    } catch {
      setMembers([])
    } finally {
      setLoading(false)
    }
  }, [current])

  useEffect(() => {
    void loadMembers()
  }, [loadMembers])

  async function createGroup(e: React.FormEvent) {
    e.preventDefault()
    const label = name.trim()
    if (!label || busy) return
    setBusy(true)
    try {
      const created = await spaceApi.create(label)
      setName('')
      await refresh()
      toast.success(`已创建「${created.name}」`)
    } catch (err) {
      toast.success(err instanceof ApiError ? err.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }

  async function addMember(e: React.FormEvent) {
    e.preventDefault()
    const username = invite.trim()
    if (!username || !current || busy) return
    setBusy(true)
    try {
      await spaceApi.addMember(current.id, username)
      setInvite('')
      await loadMembers()
      toast.success(`已把 ${username} 加进小组`)
    } catch (err) {
      toast.success(err instanceof ApiError ? err.message : '添加失败')
    } finally {
      setBusy(false)
    }
  }

  async function removeMember(member: MemberInfo) {
    if (!current) return
    try {
      await spaceApi.removeMember(current.id, member.user_id)
      await loadMembers()
      toast.success(`已移除 ${member.display_name || member.username}`)
    } catch (err) {
      toast.success(err instanceof ApiError ? err.message : '移除失败')
    }
  }

  async function toggleRole(member: MemberInfo) {
    if (!current) return
    const next = member.role === 'owner' ? 'member' : 'owner'
    try {
      await spaceApi.setRole(current.id, member.user_id, next)
      await loadMembers()
      toast.success(next === 'owner' ? '已设为创建者' : '已降为普通成员')
    } catch (err) {
      toast.success(err instanceof ApiError ? err.message : '修改失败')
    }
  }

  function renderSpace(space: SpaceInfo) {
    const active = space.id === current?.id
    return (
      <button
        key={space.id}
        type="button"
        className={active ? 'space-row active' : 'space-row'}
        onClick={() => (active ? undefined : switchTo(space.id))}
      >
        <span className="space-icon">
          {space.kind === 'personal' ? <Users size={16} /> : <Crown size={16} />}
        </span>
        <span className="space-main">
          <span className="space-name">{space.name}</span>
          <span className="space-meta">
            {space.kind === 'personal' ? '个人空间' : `学习小组 · ${space.member_count} 人`}
            {space.my_role === 'owner' ? ' · 我创建的' : ''}
          </span>
        </span>
        {active ? <Check size={16} className="space-check" /> : null}
      </button>
    )
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>空间与成员</h1>
          <p className="page-desc">
            个人空间只属于你；学习小组里的资料、笔记、练习和错题，组内成员共享。
          </p>
        </div>
      </header>

      <div className="space-grid">
        <section className="card">
          <h2 className="card-title">我的空间</h2>
          <div className="space-list">{spaces.map(renderSpace)}</div>

          <form className="inline-form" onSubmit={createGroup}>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="新建学习小组，比如「密码学复习组」"
              maxLength={64}
            />
            <button className="btn btn-primary" type="submit" disabled={busy || !name.trim()}>
              <Plus size={16} />
              <span>建组</span>
            </button>
          </form>
        </section>

        <section className="card">
          <h2 className="card-title">
            成员
            {current ? <span className="card-sub"> · {current.name}</span> : null}
          </h2>

          {loading ? (
            <Spinner text="正在读取成员…" />
          ) : members.length === 0 ? (
            <Empty title="还没有成员" description="在下面输入用户名把同学加进来。" />
          ) : (
            <ul className="member-list">
              {members.map((m) => (
                <li key={m.user_id} className="member-row">
                  <span className="avatar">{(m.display_name || m.username).slice(0, 1)}</span>
                  <span className="member-main">
                    <span className="member-name">
                      {m.display_name || m.username}
                      {m.user_id === user?.id ? <em className="me-tag">我</em> : null}
                    </span>
                    <span className="member-sub">@{m.username}</span>
                  </span>
                  <span className={m.role === 'owner' ? 'role-tag owner' : 'role-tag'}>
                    {m.role === 'owner' ? '创建者' : '成员'}
                  </span>
                  {isOwner && !isPersonal ? (
                    <span className="member-actions">
                      <button
                        type="button"
                        className="icon-btn"
                        title={m.role === 'owner' ? '降为普通成员' : '设为创建者'}
                        onClick={() => void toggleRole(m)}
                      >
                        <Crown size={15} />
                      </button>
                      <button
                        type="button"
                        className="icon-btn danger"
                        title="移出小组"
                        onClick={() => void removeMember(m)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}

          {isPersonal ? (
            <p className="hint">个人空间不能加成员。先建一个学习小组，再把人加进去。</p>
          ) : isOwner ? (
            <form className="inline-form" onSubmit={addMember}>
              <input
                className="input"
                value={invite}
                onChange={(e) => setInvite(e.target.value)}
                placeholder="输入对方的用户名"
              />
              <button className="btn" type="submit" disabled={busy || !invite.trim()}>
                <UserPlus size={16} />
                <span>加入</span>
              </button>
            </form>
          ) : (
            <p className="hint">只有小组创建者能加人或移除成员。</p>
          )}
        </section>
      </div>
    </div>
  )
}
