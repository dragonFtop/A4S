import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  ApiError,
  authApi,
  getSpaceId,
  getToken,
  setSpaceId,
  setToken,
  type MeResult,
  type SpaceInfo,
  type UserInfo,
} from '../api/client'

interface SpaceContextValue {
  user: UserInfo | null
  spaces: SpaceInfo[]
  current: SpaceInfo | null
  ready: boolean
  /** 切空间：写入请求头用的 id，然后整页重载。
   *
   *  为什么直接 reload 而不是局部刷新：每个页面在挂载时各自拉数据，
   *  没有一个统一的失效机制。切空间后让它们重挂一遍，比给 9 个页面
   *  各加一套订阅逻辑更可靠，也不会出现"半个页面的数据还属于旧空间"。 */
  switchTo: (spaceId: string) => void
  refresh: () => Promise<void>
  logout: () => Promise<void>
}

const SpaceContext = createContext<SpaceContextValue | null>(null)

export function SpaceProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserInfo | null>(null)
  const [spaces, setSpaces] = useState<SpaceInfo[]>([])
  const [ready, setReady] = useState(false)

  const apply = useCallback((data: MeResult) => {
    setUser(data.user)
    setSpaces(data.spaces)
  }, [])

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null)
      setSpaces([])
      setReady(true)
      return
    }
    try {
      const data = await authApi.me()
      apply(data)
      // 本地记的空间可能已被删除或移出，回落到当前返回的空间
      const saved = getSpaceId()
      if (!saved || !data.spaces.some((s) => s.id === saved)) {
        setSpaceId(data.current_space.id)
      }
    } catch (err) {
      // 401 由请求层统一处理，这里只兜网络错误
      if (!(err instanceof ApiError) || err.status !== 401) {
        setUser(null)
        setSpaces([])
      }
    } finally {
      setReady(true)
    }
  }, [apply])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const switchTo = useCallback((spaceId: string) => {
    setSpaceId(spaceId)
    window.location.reload()
  }, [])

  const logout = useCallback(async () => {
    try {
      await authApi.logout()
    } catch {
      // 登出失败也要清本地，否则会卡在"点了没反应"
    }
    setToken(null)
    setSpaceId(null)
    setUser(null)
    setSpaces([])
  }, [])

  const current = useMemo(
    () => spaces.find((s) => s.id === getSpaceId()) ?? spaces[0] ?? null,
    [spaces],
  )

  const value = useMemo(
    () => ({ user, spaces, current, ready, switchTo, refresh, logout }),
    [user, spaces, current, ready, switchTo, refresh, logout],
  )

  return <SpaceContext.Provider value={value}>{children}</SpaceContext.Provider>
}

export function useSpace(): SpaceContextValue {
  const ctx = useContext(SpaceContext)
  if (!ctx) throw new Error('useSpace 必须在 SpaceProvider 内使用')
  return ctx
}

/** 当前空间是否可写：被共享的 viewer 只能看与问，不能写（后端同样返回 403）。 */
export function useCanWrite(): boolean {
  const { current } = useSpace()
  return current?.my_role !== 'viewer'
}
