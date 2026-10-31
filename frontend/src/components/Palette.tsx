import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { Icon } from './icons'
import { Time, useDialog } from './ui'
import { NAV } from './nav'
import { emojiFor } from '../lib/types'

type Res = {
  contacts: { id: number; display_name: string; kind: string; match?: string }[]
  pets: { id: number; name: string; species: string; contact: { id: number; display_name: string } }[]
  tasks: { id: number; title: string; status: string }[]
  messages: { id: number; contact_id: number; contact_name: string; preview: string; at: string }[]
}
type Item = { key: string; section: string; icon: string; label: string; sub?: string; at?: string; to: string }

export function Palette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { can, me } = useApp()
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Res | null>(null)
  const [busy, setBusy] = useState(false)
  const [active, setActive] = useState(0)
  const box = useRef<HTMLDivElement>(null)
  const seq = useRef(0)
  useDialog(open, onClose, box)
  useEffect(() => { if (open) { setQ(''); setRes(null); setActive(0) } }, [open])
  useEffect(() => {
    if (!open) return
    const t = q.trim()
    if (t.length < 2 || !can('contacts.view')) { setRes(null); setBusy(false); return }
    setBusy(true)
    const my = ++seq.current
    const h = window.setTimeout(async () => {
      try {
        const r = await api<Res>('/search?q=' + encodeURIComponent(t), { quiet: true })
        if (my === seq.current) { setRes(r); setActive(0) }
      } catch { if (my === seq.current) setRes(null) }
      if (my === seq.current) setBusy(false)
    }, 180)
    return () => clearTimeout(h)
  }, [q, open, can])

  const items = useMemo<Item[]>(() => {
    const out: Item[] = []
    const t = q.trim().toLowerCase()
    const acts: Item[] = []
    if (can('contacts.edit')) acts.push({ key: 'a-add', section: 'Actions', icon: 'plus', label: 'Add a contact', to: '/contacts?add=1' })
    if (can('tasks.work')) acts.push({ key: 'a-task', section: 'Actions', icon: 'plus', label: 'New task', to: '/tasks?new=1' })
    out.push(...acts.filter((a) => !t || a.label.toLowerCase().includes(t)))
    if (res) {
      res.contacts.forEach((c) => out.push({ key: 'c' + c.id, section: 'People', icon: 'user', label: c.display_name, sub: [c.kind === 'patient' ? 'Patient' : 'Inquiry', c.match].filter(Boolean).join(' · '), to: `/contacts/${c.id}` }))
      res.pets.forEach((p) => out.push({ key: 'p' + p.id, section: 'Pets', icon: 'paw', label: p.name, sub: `${p.species}, ${p.contact.display_name}`, to: `/contacts/${p.contact.id}?tab=pets` }))
      res.tasks.forEach((k) => out.push({ key: 't' + k.id, section: 'Tasks', icon: 'tasks', label: k.title, sub: k.status === 'in_progress' ? 'In progress' : k.status === 'done' ? 'Done' : 'Open', to: `/tasks?open=${k.id}` }))
      res.messages.forEach((m) => out.push({ key: 'm' + m.id, section: 'Messages', icon: 'chat', label: m.preview, sub: m.contact_name, at: m.at, to: `/contacts/${m.contact_id}` }))
    }
    NAV.forEach((g) => g.items.forEach((i) => {
      if (i.admin ? !me?.user.is_admin : i.perm && !can(i.perm)) return
      if (!t || i.label.toLowerCase().includes(t)) out.push({ key: 'g' + i.to, section: 'Go to', icon: i.icon, label: i.label, to: i.to })
    }))
    return out
  }, [q, res, can, me])

  if (!open) return null
  function pick(i: Item | undefined) { if (!i) return; onClose(); nav(i.to) }
  function key(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter') { e.preventDefault(); pick(items[active]) }
  }
  let last = ''
  return (
    <div className="scrim top" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Search and go to" ref={box}>
        <div className="pal-in">
          <Icon n="search" size={20} />
          <input data-autofocus value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={key} placeholder="Search people, pets, tasks and messages" role="combobox" aria-expanded="true" aria-controls="pal-list" aria-activedescendant={items[active] ? 'pal-' + items[active].key : undefined} autoComplete="off" maxLength={80} aria-label="Search" />
          <kbd>Esc</kbd>
        </div>
        <div className="pal-l" id="pal-list" role="listbox">
          {items.map((it, i) => {
            const head = it.section !== last
            last = it.section
            return (
              <div key={it.key}>
                {head && <div className="pal-s">{it.section}</div>}
                <button id={'pal-' + it.key} role="option" aria-selected={i === active} className={'pal-i' + (i === active ? ' on' : '')} onMouseMove={() => setActive(i)} onClick={() => pick(it)} tabIndex={-1}>
                  <span className="pal-ic">{it.icon === 'paw' ? <span aria-hidden="true">{emojiFor(it.sub?.split(',')[0] || '')}</span> : <Icon n={it.icon} size={16} />}</span>
                  <span className="pal-t">{it.label}</span>
                  {it.sub && <span className="pal-sub">{it.sub}</span>}
                  {it.at && <Time iso={it.at} className="pal-sub" />}
                </button>
              </div>
            )
          })}
          {busy && <p className="pal-note">Searching</p>}
          {!busy && q.trim().length >= 2 && res && !res.contacts.length && !res.pets.length && !res.tasks.length && !res.messages.length && <p className="pal-note">No people, pets, tasks or messages match "{q.trim()}".</p>}
          {q.trim().length === 1 && <p className="pal-note">Type at least 2 letters to search.</p>}
        </div>
        <div className="pal-f"><span><kbd>Up</kbd> <kbd>Down</kbd> to move</span><span><kbd>Enter</kbd> to open</span></div>
      </div>
    </div>
  )
}
