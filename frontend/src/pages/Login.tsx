import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, authApi, setSpaceId, setToken } from '../api/client'
import { Spinner } from '../components/ui/Feedback'

type Mode = 'login' | 'register'

export default function Login() {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (busy) return
    setError('')
    setBusy(true)
    try {
      const result =
        mode === 'login'
          ? await authApi.login(username.trim(), password)
          : await authApi.register(username.trim(), password, displayName.trim())
      setToken(result.token)
      setSpaceId(result.space.id)
      // 整页跳转：让 SpaceProvider 重新拉一次 me，避免手工同步两份状态
      window.location.href = '/overview'
      navigate('/overview', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '连不上后端，请确认服务已启动')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <img className="brand-mark" src="/a4s-mark.png" alt="" aria-hidden="true" />
          <div>
            <div className="auth-title">A4S</div>
            <div className="auth-sub">把资料变成会考你的老师</div>
          </div>
        </div>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            className={mode === 'login' ? 'auth-tab active' : 'auth-tab'}
            onClick={() => {
              setMode('login')
              setError('')
            }}
          >
            登录
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            className={mode === 'register' ? 'auth-tab active' : 'auth-tab'}
            onClick={() => {
              setMode('register')
              setError('')
            }}
          >
            注册
          </button>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <label className="field">
            <span>用户名</span>
            <input
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="字母、数字、下划线，3～32 位"
              required
            />
          </label>

          {mode === 'register' ? (
            <label className="field">
              <span>昵称（可留空）</span>
              <input
                className="input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="小组里显示的名字"
              />
            </label>
          ) : null}

          <label className="field">
            <span>密码</span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              placeholder="至少 6 位"
              required
            />
          </label>

          {error ? <div className="auth-error">{error}</div> : null}

          <button className="btn btn-primary auth-submit" type="submit" disabled={busy}>
            {busy ? <Spinner text="" /> : null}
            <span>{mode === 'login' ? '登录' : '注册并进入'}</span>
          </button>
        </form>

        <p className="auth-hint">
          {mode === 'login'
            ? '还没有账号？切到「注册」。'
            : '注册会自动创建一个个人空间。'}
        </p>
      </div>
    </div>
  )
}
