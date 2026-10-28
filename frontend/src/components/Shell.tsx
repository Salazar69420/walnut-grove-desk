import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Assistant } from './Assistant'

export function Shell() {
  const { me, can, setMe, theme, setTheme, run, busy } = useApp()
  const nav = useNavigate()
  const cbs = useLive<{ items: any[] }>(can('callbacks.view') ? '/callbacks' : null, ['callbacks'])
  const ov = useLive<{ summary: { pending: number } }>(can('overnight.view') ? '/overnight' : null, ['overnight'])
  const rs = useLive<{ items: any[] }>(can('results.view') ? '/results' : null, ['results'])
  const settingsLive = useLive<any>(null, ['settings'])
  void settingsLive
  if (!me) return null
  const u = me.user
  const openCb = cbs.data ? cbs.data.items.filter((c) => c.status !== 'done').length : null
  const hotCb = cbs.data ? cbs.data.items.some((c) => c.status !== 'done' && c.priority === 'high') : false
  const pend = ov.data ? ov.data.summary.pending : null
  const openRs = rs.data ? rs.data.items.filter((r) => r.stage < 2).length : null
  const items: [string, string, string, number | null, boolean][] = [
    ['/brief', 'Brief', 'brief.view', null, false],
    ['/overnight', 'Overnight', 'overnight.view', pend, (pend || 0) > 0],
    ['/callbacks', 'Callbacks', 'callbacks.view', openCb, (openCb || 0) > 0 && hotCb],
    ['/results', 'Lab results', 'results.view', openRs, false],
    ['/schedule', 'Schedule', 'schedule.view', null, false],
    ['/setup', 'After-hours setup', 'afterhours.view', null, false],
    ['/tools', 'How it fits', 'tools.view', null, false],
  ]
  async function signOut() {
    await run('signout', async () => {
      try { await api('/auth/logout', { method: 'POST', quiet: true }) } catch { /* ignore */ }
      setMe(null)
      nav('/login', { replace: true })
    })
  }
  return (
    <div className="app">
      <div className="top">
        <div className="brand"><div className="logo">W</div>Walnut Grove Desk</div>
        {me.settings.banner_on && <span className="pill">Prototype, example data</span>}
        <div className="spacer" />
        <span style={{ color: 'var(--mute)', fontSize: 13 }}>Signed in as</span>
        <b>{u.display_name}</b>
        <div className="seg" role="group" aria-label="Theme">
          {(['light', 'dark', 'auto'] as const).map((t) => (
            <button key={t} aria-pressed={theme === t} onClick={() => setTheme(t)}>{t === 'light' ? 'Light' : t === 'dark' ? 'Dark' : 'Auto'}</button>
          ))}
        </div>
        <button className="link" onClick={signOut} disabled={busy.has('signout')}>Sign out</button>
      </div>
      <div className="main-grid">
        <nav className="side" aria-label="Sections">
          <div className="me"><div className="av">{u.initials}</div><div><b>{u.display_name}</b><span>{u.title}</span></div></div>
          {items.filter((i) => can(i[2])).map(([to, label, , count, hot]) => (
            <NavLink key={to} to={to} className="nav" aria-current={undefined} style={{ textDecoration: 'none' }}>
              {({ isActive }) => (
                <>
                  <span aria-current={isActive ? 'page' : undefined} style={{ flex: 1 }}>{label}</span>
                  {count !== null && <span className={'count' + (hot ? ' hot' : '')}>{count}</span>}
                </>
              )}
            </NavLink>
          ))}
          {u.is_admin && (
            <NavLink to="/admin" className="nav" style={{ textDecoration: 'none' }}>
              {({ isActive }) => <span aria-current={isActive ? 'page' : undefined}>Admin</span>}
            </NavLink>
          )}
        </nav>
        <main className="work" id="work"><Outlet /></main>
        {can('assistant.use') && <Assistant />}
      </div>
    </div>
  )
}
