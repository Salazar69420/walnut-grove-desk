import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { onEntity } from '../live'
import { useApp } from '../ctx'
import { Icon } from './icons'

type Person = { id: number; name: string; initials: string; title: string }
type Thread = { id: number; kind: 'everyone' | 'direct' | 'group'; title: string; members: Person[]; last: { body: string; at: string; mine: boolean; who: string } | null; unread: number }
type Msg = { id: number; body: string; at: string; mine: boolean; who: { id: number; name: string; initials: string } | null; contact: { id: number; name: string } | null }

const hue = (id: number) => (id * 67 + 23) % 360
const clock = (iso: string) => {
  const d = new Date(iso)
  const today = new Date().toDateString() === d.toDateString()
  return today ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

/** The unread count for the Chats button; updates live. */
export function useChatUnread() {
  const [n, setN] = useState(0)
  const load = useCallback(() => api<{ unread: number }>('/chats', { quiet: true }).then((r) => setN(r.unread)).catch(() => {}), [])
  useEffect(() => {
    load()
    const off = onEntity('chats', () => { load() })
    window.addEventListener('wgd-chat-read', load)  // this person read a chat: refresh their own badge
    return () => { off(); window.removeEventListener('wgd-chat-read', load) }
  }, [load])
  return n
}

/** Team chat: the whole team, one-to-one and group conversations with colleagues. Staff only; clients never see it. */
export function Chats() {
  const { toast } = useApp()
  const [threads, setThreads] = useState<Thread[]>([])
  const [staff, setStaff] = useState<Person[]>([])
  const [open, setOpen] = useState<Thread | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [picking, setPicking] = useState(false)
  const [pick, setPick] = useState<number[]>([])
  const [title, setTitle] = useState('')
  const [q, setQ] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const openRef = useRef<Thread | null>(null)
  openRef.current = open

  const loadList = useCallback(() => api<{ items: Thread[]; staff: Person[] }>('/chats', { quiet: true }).then((r) => { setThreads(r.items); setStaff(r.staff) }).catch(() => {}), [])
  const loadMsgs = useCallback((t: Thread) => api<{ items: Msg[]; thread: Thread }>(`/chats/${t.id}/messages`, { quiet: true }).then((r) => { if (openRef.current?.id === t.id) { setMsgs(r.items); setOpen(r.thread) } window.dispatchEvent(new Event('wgd-chat-read')) }).catch(() => {}), [])
  useEffect(() => { loadList() }, [loadList])
  useEffect(() => onEntity('chats', () => { loadList(); const cur = openRef.current; if (cur) loadMsgs(cur) }), [loadList, loadMsgs])
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }) }, [msgs])

  function show(t: Thread) { setOpen(t); setMsgs([]); setPicking(false); loadMsgs(t) }
  async function send(e: FormEvent) {
    e.preventDefault()
    const body = text.trim()
    if (!body || !open) return
    setText('')
    const temp: Msg = { id: -Date.now(), body, at: new Date().toISOString(), mine: true, who: null, contact: null }
    setMsgs((m) => [...m, temp])
    try { const r = await api<{ item: Msg }>(`/chats/${open.id}/messages`, { body: { body }, quiet: true }); setMsgs((m) => m.map((x) => (x.id === temp.id ? r.item : x))) }
    catch (x) { setMsgs((m) => m.filter((y) => y.id !== temp.id)); setText(body); toast(x instanceof ApiError && x.message !== 'error' ? x.message : 'Not sent. Try again.') }
  }
  async function start() {
    try { const r = await api<{ item: Thread }>('/chats', { body: { user_ids: pick, title }, quiet: true }); setPick([]); setTitle(''); loadList(); show(r.item) }
    catch (x) { toast(x instanceof ApiError && x.message !== 'error' ? x.message : 'Could not start the chat.') }
  }

  if (open) return (
    <div className="chat">
      <header className="chat-h">
        <button className="ibtn" onClick={() => { setOpen(null); loadList() }} aria-label="Back to all chats"><Icon n="chevL" /></button>
        <div><b>{open.title}</b><span>{open.kind === 'everyone' ? 'The whole team' : open.kind === 'group' ? open.members.map((m) => m.name.split(' ')[0]).join(', ') + ' and you' : open.members[0]?.title || 'Direct message'}</span></div>
      </header>
      <div className="chat-msgs" ref={box} aria-live="polite">
        {msgs.length === 0 && <p className="mute chat-empty">No messages yet. Say hello.</p>}
        {msgs.map((m, i) => {
          const showWho = !m.mine && (i === 0 || msgs[i - 1].who?.id !== m.who?.id)
          return (
            <div key={m.id} className={'cm' + (m.mine ? ' me' : '')}>
              {showWho && m.who && <span className="cm-who">{m.who.name}</span>}
              <div className="cm-b">{m.body}{m.contact && <Link className="cm-link" to={`/contacts/${m.contact.id}`}>{m.contact.name}</Link>}</div>
              <span className="cm-t">{m.id < 0 ? 'Sending...' : clock(m.at)}</span>
            </div>
          )
        })}
      </div>
      <form className="send" onSubmit={send}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={`Message ${open.kind === 'everyone' ? 'everyone' : open.title}`} maxLength={4000} aria-label="Message" autoFocus />
        <button className="btn primary" type="submit" disabled={!text.trim()}>Send</button>
      </form>
    </div>
  )

  const people = staff.filter((s) => !q || s.name.toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="chat">
      <header className="chat-h"><div><b>Chats</b><span>Talk to the team. Only staff can see these.</span></div>
        <button className="btn small" onClick={() => { setPicking(!picking); setPick([]) }}>{picking ? 'Cancel' : 'New chat'}</button></header>
      {picking ? (
        <div className="chat-pick">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search colleagues" aria-label="Search colleagues" />
          {people.map((p) => (
            <label key={p.id} className="chat-person"><input type="checkbox" checked={pick.includes(p.id)} onChange={(e) => setPick(e.target.checked ? [...pick, p.id] : pick.filter((x) => x !== p.id))} />
              <span className="cav" style={{ ['--h' as any]: hue(p.id) }}>{p.initials}</span><span><b>{p.name}</b><small>{p.title}</small></span></label>
          ))}
          {pick.length > 1 && <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Group name (optional)" aria-label="Group name" maxLength={80} />}
          <button className="btn primary" disabled={!pick.length} onClick={start}>{pick.length > 1 ? `Start a group with ${pick.length}` : 'Start chat'}</button>
        </div>
      ) : (
        <div className="chat-list">
          {threads.map((t) => (
            <button key={t.id} className={'chat-row' + (t.unread ? ' unread' : '')} onClick={() => show(t)}>
              <span className="cav" style={{ ['--h' as any]: t.kind === 'everyone' ? 165 : hue(t.members[0]?.id || t.id) }}>{t.kind === 'everyone' ? 'All' : t.kind === 'group' ? t.members.length + 1 : t.members[0]?.initials}</span>
              <span className="chat-row-m"><b>{t.title}</b><small>{t.last ? `${t.last.mine ? 'You' : t.last.who.split(' ')[0]}: ${t.last.body}` : t.kind === 'everyone' ? 'The whole team' : 'No messages yet'}</small></span>
              <span className="chat-row-r">{t.last && <small>{clock(t.last.at)}</small>}{t.unread > 0 && <span className="count hot">{t.unread}</span>}</span>
            </button>
          ))}
          {staff.length > 0 && <div className="chat-quick"><small className="mute">Message someone</small>{staff.slice(0, 8).map((p) => (
            <button key={p.id} className="chat-chip" onClick={() => api<{ item: Thread }>('/chats', { body: { user_ids: [p.id] }, quiet: true }).then((r) => { loadList(); show(r.item) }).catch(() => {})}>
              <span className="cav sm" style={{ ['--h' as any]: hue(p.id) }}>{p.initials}</span>{p.name.split(' ')[0]}</button>))}</div>}
        </div>
      )}
    </div>
  )
}
