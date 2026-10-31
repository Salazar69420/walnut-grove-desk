import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useLive } from '../live'
import { Icon } from './icons'
import { Pop, Time } from './ui'
import type { Notif } from '../lib/types'

type Data = { items: Notif[]; unread: number }

export function Bell() {
  const nav = useNavigate()
  const nt = useLive<Data>('/notifications', ['notifications'])
  const [snap, setSnap] = useState<Set<number>>(new Set())
  const timer = useRef<number>()
  const items = nt.data?.items || []
  const unread = nt.data?.unread ?? 0
  const ref = useRef(nt)
  ref.current = nt
  useEffect(() => () => clearTimeout(timer.current), [])

  function markAll() {
    const d = ref.current.data
    if (!d || d.unread === 0) return
    ref.current.setData({ items: d.items.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() })), unread: 0 })
    api('/notifications/read-all', { method: 'POST', quiet: true }).catch(() => ref.current.reload())
  }
  function opened() {
    const d = ref.current.data
    setSnap(new Set((d?.items || []).filter((n) => !n.read_at).map((n) => n.id)))
    clearTimeout(timer.current)
    timer.current = window.setTimeout(markAll, 1500)
  }
  function go(n: Notif, close: () => void) {
    if (!n.read_at) {
      const d = ref.current.data
      if (d) ref.current.setData({ items: d.items.map((x) => x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x), unread: Math.max(0, d.unread - 1) })
      api(`/notifications/${n.id}/read`, { method: 'POST', quiet: true }).catch(() => ref.current.reload())
    }
    close()
    if (n.link && n.link.startsWith('/')) nav(n.link)
  }
  return (
    <Pop align="right" onOpen={opened}
      button={({ open, toggle }) => (
        <button className="ibtn bell" onClick={toggle} aria-expanded={open} aria-haspopup="dialog" aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}>
          <Icon n="bell" size={20} />
          {unread > 0 && <span className="bdg" aria-hidden="true">{unread > 9 ? '9+' : unread}</span>}
        </button>
      )}>
      {(close) => (
        <div className="notif" role="dialog" aria-label="Notifications">
          <div className="notif-h"><b>Notifications</b><button className="link" onClick={markAll} disabled={unread === 0}>Mark all read</button></div>
          <div className="notif-l">
            {nt.loading && !nt.data && <div className="pad"><span className="sk" style={{ width: '70%', height: 14 }} /></div>}
            {nt.data && items.length === 0 && <p className="pad mute">Nothing new. Mentions and assigned tasks show up here.</p>}
            {items.map((n) => (
              <button key={n.id} className={'notif-i' + (snap.has(n.id) || !n.read_at ? ' new' : '')} onClick={() => go(n, close)}>
                <span className={'nk k-' + n.kind}><Icon n={n.kind === 'mention' ? 'user' : n.kind === 'reminder' ? 'clock' : 'tasks'} size={16} /></span>
                <span className="nt"><span>{n.text}</span><Time iso={n.created_at} className="mute" /></span>
                {(snap.has(n.id) || !n.read_at) && <i className="udot" aria-label="Unread" />}
              </button>
            ))}
          </div>
        </div>
      )}
    </Pop>
  )
}
