import { useEffect, useRef, useState } from 'react'
import { Outlet, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Assistant } from './Assistant'
import { Bell } from './Bell'
import { Chats, useChatUnread } from './Chats'
import { Palette } from './Palette'
import { Icon } from './icons'
import { Pop, useDialog } from './ui'
import { NAV, isWorkspace } from './nav'

export function Shell() {
  const { me, can, setMe, theme, setTheme, run, busy } = useApp()
  const nav = useNavigate()
  const loc = useLocation()
  const [pal, setPal] = useState(false)
  const [asst, setAsst] = useState(false)
  const [chats, setChats] = useState(false)
  const cbs = useLive<{ items: any[] }>(can('callbacks.view') ? '/callbacks' : null, ['callbacks'])
  const ov = useLive<{ summary: { pending: number } }>(can('overnight.view') ? '/overnight' : null, ['overnight'])
  const rs = useLive<{ items: any[] }>(can('results.view') ? '/results' : null, ['results'])
  const tk = useLive<{ counts: { open: number; in_progress: number; mine: number } }>(can('tasks.view') ? '/tasks?status=active&assigned=all' : null, ['tasks'])
  const settingsLive = useLive<any>(null, ['settings'])
  void settingsLive

  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && can('contacts.view')) { e.preventDefault(); setPal((p) => !p) }
    }
    document.addEventListener('keydown', f)
    return () => document.removeEventListener('keydown', f)
  }, [can])
  useEffect(() => { setAsst(false) }, [loc.pathname])
  useEffect(() => { document.getElementById('work')?.focus({ preventScroll: true }) }, [loc.pathname])

  if (!me) return null
  const u = me.user
  const ws = isWorkspace(loc.pathname)
  const fit = /^\/contacts\/\d+/.test(loc.pathname) || loc.pathname === '/tasks'
  const openCb = cbs.data ? cbs.data.items.filter((c) => c.status !== 'done').length : null
  const hotCb = cbs.data ? cbs.data.items.some((c) => c.status !== 'done' && c.priority === 'high') : false
  const pend = ov.data ? ov.data.summary.pending : null
  const openRs = rs.data ? rs.data.items.filter((r) => r.stage < 2).length : null
  const taskCount = tk.data ? tk.data.counts.open + tk.data.counts.in_progress : null
  const counts: Record<string, [number | null, boolean]> = {
    '/tasks': [taskCount, false],
    '/overnight': [pend, (pend || 0) > 0],
    '/callbacks': [openCb, (openCb || 0) > 0 && hotCb],
    '/results': [openRs, false],
  }
  async function signOut() {
    await run('signout', async () => {
      try { await api('/auth/logout', { method: 'POST', quiet: true }) } catch { /* ignore */ }
      setMe(null)
      nav('/login', { replace: true })
    })
  }
  const canAssist = can('assistant.use')
  return (
    <div className="app">
      <a className="skip" href="#work">Skip to content</a>
      <div className="top">
        <div className="brand"><div className="logo">W</div><span className="brand-t">Walnut Grove Desk</span></div>
        {me.settings.banner_on && <span className="pill hide-s">Prototype, example data</span>}
        {can('contacts.view') && (
          <button className="searchbtn" onClick={() => setPal(true)} aria-label="Search people, pets, tasks and messages">
            <Icon n="search" size={18} /><span className="st">Search people, pets, tasks</span><kbd className="hide-s">{/Mac|iPhone|iPad/.test(navigator.platform) ? 'Cmd K' : 'Ctrl K'}</kbd>
          </button>
        )}
        <div className="spacer" />
        {ws && canAssist && <button className="btn asst-btn" onClick={() => setAsst((a) => !a)} aria-expanded={asst}><Icon n="sparkle" size={16} /><span>Assistant</span></button>}
        <ChatsButton open={chats} onClick={() => { setChats((c) => !c); setAsst(false) }} />
        <Bell />
        <Pop align="right" button={({ open, toggle }) => (
          <button className="umenu" onClick={toggle} aria-expanded={open} aria-haspopup="menu" aria-label={`Account menu for ${u.display_name}`}>
            <span className="av">{u.initials}</span><span className="un hide-s">{u.display_name}</span><Icon n="chevD" size={14} />
          </button>
        )}>
          {(close) => (
            <div className="menu" role="menu">
              <div className="menu-h"><b>{u.display_name}</b><span>{u.title}</span></div>
              <div className="menu-s">Theme</div>
              <div className="seg" role="group" aria-label="Theme">
                {(['light', 'dark', 'auto'] as const).map((t) => (
                  <button key={t} aria-pressed={theme === t} onClick={() => setTheme(t)}>{t === 'light' ? 'Light' : t === 'dark' ? 'Dark' : 'Auto'}</button>
                ))}
              </div>
              <button className="menu-i" role="menuitem" onClick={() => { close(); signOut() }} disabled={busy.has('signout')}>Sign out</button>
            </div>
          )}
        </Pop>
      </div>
      <div className={'main-grid' + (ws ? ' ws' : '')}>
        <nav className="side" aria-label="Sections">
          <div className="me"><div className="av">{u.initials}</div><div><b>{u.display_name}</b><span>{u.title}</span></div></div>
          {NAV.map((g) => {
            const list = g.items.filter((i) => i.admin ? u.is_admin : !i.perm || can(i.perm))
            if (!list.length) return null
            return (
              <div className="navg" key={g.group} role="group" aria-label={g.group}>
                <div className="navh">{g.group}</div>
                {list.map((i) => {
                  const [count, hot] = counts[i.to] || [null, false]
                  if (i.href) return (
                    <a key={i.to} href={i.href} target="_blank" rel="noreferrer" className="nav" style={{ textDecoration: 'none' }} title={`${i.label} opens in a new tab`}>
                      <Icon n={i.icon} size={16} /><span style={{ flex: 1 }}>{i.label}</span><span className="mute" aria-hidden="true">↗</span>
                    </a>
                  )
                  return (
                    <NavLink key={i.to} to={i.to} className="nav" style={{ textDecoration: 'none' }}>
                      {({ isActive }) => (
                        <>
                          <Icon n={i.icon} size={16} />
                          <span aria-current={isActive ? 'page' : undefined} style={{ flex: 1 }}>{i.label}</span>
                          {count !== null && count !== undefined && <span className={'count' + (hot ? ' hot' : '')}>{count}</span>}
                        </>
                      )}
                    </NavLink>
                  )
                })}
              </div>
            )
          })}
        </nav>
        <main className={'work' + (fit ? ' fit' : '')} id="work" tabIndex={-1}><Outlet /></main>
        {canAssist && !ws && <Assistant />}
      </div>
      {canAssist && ws && <Drawer open={asst} onClose={() => setAsst(false)}><Assistant /></Drawer>}
      <Drawer open={chats} onClose={() => setChats(false)} label="Chats">{chats && <Chats />}</Drawer>
      <Palette open={pal} onClose={() => setPal(false)} />
    </div>
  )
}

function ChatsButton({ open, onClick }: { open: boolean; onClick: () => void }) {
  const unread = useChatUnread()
  return (
    <button className="btn asst-btn chats-btn" onClick={onClick} aria-expanded={open} aria-label={unread ? `Chats, ${unread} unread` : 'Chats'}>
      <Icon n="chat" size={16} /><span className="hide-s">Chats</span>{unread > 0 && <span className="count hot">{unread > 99 ? '99+' : unread}</span>}
    </button>
  )
}

function Drawer({ open, onClose, children, label = 'Assistant' }: { open: boolean; onClose: () => void; children: React.ReactNode; label?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useDialog(open, onClose, ref)
  return (
    <>
      <div className={'drawer-scrim' + (open ? ' open' : '')} onClick={onClose} aria-hidden="true" />
      <div className={'drawer' + (open ? ' open' : '')} ref={ref} role="dialog" aria-label={label} aria-hidden={!open} aria-modal={open}>
        <button className="ibtn drawer-x" onClick={onClose} aria-label={`Close ${label.toLowerCase()}`}><Icon n="x" /></button>
        {children}
      </div>
    </>
  )
}
