import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { onEntity } from '../live'
import { Avatar, Clock, Modal, Skeleton } from './ui'
import { Icon } from './icons'
import { CallButtons, PlayRow, TranscriptModal, usePlayer } from './CallTools'
import { bytes, dayKey, dayLabel, exact, firstName } from '../lib/fmt'
import { VISIT_LABEL, type Row, type TItem } from '../lib/types'

const keyOf = (i: TItem) => `${i.type}-${i.id}`
const sortDesc = (a: TItem, b: TItem) => (a.at < b.at ? 1 : a.at > b.at ? -1 : String(b.id).localeCompare(String(a.id), undefined, { numeric: true }))

export function useTimeline(cid: number, channel: string) {
  const [items, setItems] = useState<TItem[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [older, setOlder] = useState(false)
  const [error, setError] = useState(false)
  const [pending, setPending] = useState<TItem[]>([])
  const seq = useRef(0)
  const nextRef = useRef<string | null>(null)
  const base = `/contacts/${cid}/timeline?limit=30&channel=${channel}`

  const refresh = useCallback(async () => {
    const my = seq.current
    try {
      const r = await api<{ items: TItem[]; next_before: string | null }>(base, { quiet: true })
      if (my !== seq.current) return
      setItems((prev) => { const m = new Map(prev.map((i) => [keyOf(i), i])); r.items.forEach((i) => m.set(keyOf(i), i)); return [...m.values()].sort(sortDesc) })
      if (!initialDone.current) { initialDone.current = true; setNext(r.next_before); nextRef.current = r.next_before }
      setError(false)
    } catch { if (my === seq.current) setError(true) }
    if (my === seq.current) setLoading(false)
  }, [base])
  const initialDone = useRef(false)
  useEffect(() => {
    seq.current++
    initialDone.current = false
    setItems([]); setNext(null); nextRef.current = null; setLoading(true); setError(false); setPending([])
    refresh()
  }, [base]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    let t: number | undefined
    const f = () => { clearTimeout(t); t = window.setTimeout(refresh, 300) }
    const offs = ['messages', 'tasks', 'contacts'].map((e) => onEntity(e, f))
    return () => { clearTimeout(t); offs.forEach((o) => o()) }
  }, [refresh])
  // a text that is being sent: check every two seconds until the helper has answered (the server folds its result in)
  const sending = items.some((i) => i.delivery_state === 'queued')
  useEffect(() => {
    if (!sending) return
    const t = window.setInterval(refresh, 2000)
    return () => window.clearInterval(t)
  }, [sending, refresh])
  const loadOlder = useCallback(async () => {
    const nb = nextRef.current
    if (!nb || older) return false
    setOlder(true)
    const my = seq.current
    try {
      const r = await api<{ items: TItem[]; next_before: string | null }>(`${base}&before=${encodeURIComponent(nb)}`, { quiet: true })
      if (my === seq.current) {
        setItems((prev) => { const m = new Map(prev.map((i) => [keyOf(i), i])); r.items.forEach((i) => m.set(keyOf(i), i)); return [...m.values()].sort(sortDesc) })
        setNext(r.next_before); nextRef.current = r.next_before
      }
    } catch { /* ignore */ }
    setOlder(false)
    return true
  }, [base, older])
  return { items, next, loading, older, error, refresh, loadOlder, pending, setPending }
}
export type TL = ReturnType<typeof useTimeline>

function Rich({ text, names }: { text: string; names: string[] }) {
  const parts = useMemo(() => {
    if (!names.length || !text.includes('@')) return [text]
    const alts = [...new Set(names.flatMap((n) => [n, firstName(n)]))].sort((a, b) => b.length - a.length).map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    return text.split(new RegExp(`(@(?:${alts.join('|')})(?![\\p{L}]))`, 'iu'))
  }, [text, names])
  return <>{parts.map((p, i) => p.startsWith('@') && i % 2 === 1 ? <span key={i} className="mention">{p}</span> : <span key={i}>{p}</span>)}</>
}

function Body({ text, names }: { text: string; names?: string[] }) {
  const [open, setOpen] = useState(false)
  const long = text.length > 480
  return (
    <>
      <div className={'bb-body' + (long && !open ? ' clamp' : '')}><Rich text={text} names={names || []} /></div>
      {long && <button className="link-a more" onClick={() => setOpen(!open)}>{open ? 'Show less' : 'View more'}</button>}
    </>
  )
}

function Attachments({ list }: { list: TItem['attachments'] }) {
  if (!list?.length) return null
  return <div className="atts">{list.map((a) => <a key={a.id} className="achip" href={`/api/attachments/${a.id}`} download><Icon n="clip" size={14} /><span className="ell">{a.filename}</span><small>{bytes(a.bytes)}</small></a>)}</div>
}

const FAIL_TEXT: Record<string, string> = {
  practice_mode: 'Texting is in practice mode.', not_allowed_number: 'This number is not on the test list.', rate_limited_local: 'Too many texts this hour.',
  not_configured: 'The sending login is not set up.', login_failed: 'TELUS did not accept the sending login.', no_permission: 'The sending login may not send texts.',
  no_sms_number: 'The clinic has no text-capable number.', rejected: 'TELUS refused this text.', rate_limited: 'TELUS asked us to slow down.',
  sender_not_running: 'The sending helper did not answer.', error: 'The text could not be sent.',
}
function deliveryText(it: TItem): string | null {
  if (it.delivery_state === 'scheduled_dry_run') return `Scheduled for ${exact(it.scheduled_for)}. Practice only, nothing is sent.`
  if (it.delivery_state === 'dry_run') return 'Practice, not really sent'
  if (it.delivery_state === 'queued') return 'Sending through TELUS'
  if (it.delivery_state === 'sent') return 'Sent through TELUS'
  if (it.delivery_state === 'failed') return 'Not sent. ' + (FAIL_TEXT[it.delivery_code || ''] || FAIL_TEXT.error)
  return null
}
const DELIVERY_TAG: Record<string, string> = { scheduled_dry_run: 'Scheduled', dry_run: 'Practice', queued: 'Sending', sent: 'Sent', failed: 'Not sent' }

export function Item({ it, contact }: { it: TItem; contact: Row }) {
  if (it.type === 'visit' || it.type === 'task') {
    const label = it.type === 'visit' ? 'Visit' : 'Task'
    const text = [it.subject, it.body].filter(Boolean).join(': ') || label
    return (
      <div className="sysline"><span className="sl-ic"><Icon n={it.type === 'visit' ? 'stethoscope' : 'tasks'} size={14} /></span><span>{it.type === 'visit' && it.subject && VISIT_LABEL[it.subject] ? <><b>{VISIT_LABEL[it.subject]}</b>{it.body ? `: ${it.body}` : ''}</> : text}</span><Clock iso={it.at} /></div>
    )
  }
  if (it.type === 'note') {
    return (
      <div className="note-card">
        <div className="nc-h"><Icon n="note" size={15} /><b>Internal note</b><span>{it.sender?.name}</span><span className="spacer" /><span className="mute">Only your team sees this</span></div>
        <Body text={it.body || ''} names={it.mentions.map((m) => m.display_name)} />
        <Attachments list={it.attachments} />
        <div className="bb-foot"><Clock iso={it.at} /></div>
      </div>
    )
  }
  if (it.channel === 'call') return <CallLine it={it} contact={contact} />
  const out = it.direction === 'out'
  const ai = it.sender?.kind === 'ai'
  const dt = deliveryText(it)
  return (
    <div className={'brow ' + (out ? 'out' : 'in')}>
      {!out && <Avatar name={contact.display_name} initials={contact.initials} hue={contact.avatar_hue} size={28} />}
      <div className={'bb ' + (out ? 'out' : 'in') + (ai ? ' ai' : '') + (it.pending ? ' pend' : '')}>
        {it.channel === 'email' && it.subject && <div className="bb-sub">{it.subject}</div>}
        <Body text={it.body || ''} />
        <Attachments list={it.attachments} />
        <div className="bb-foot">
          {ai && out && it.channel === 'sms' && typeof it.id === 'number' && <CorrectButton messageId={it.id} reply={it.body || ''} />}
          {ai ? <span className="aitag"><Icon n="sparkle" size={12} /> AI assistant</span> : <span>{out ? it.sender?.name : it.sender?.name || contact.display_name}</span>}
          <span aria-hidden="true">·</span><span>{it.channel === 'email' ? 'Email' : 'Text'}</span>
          <span aria-hidden="true">·</span>{it.pending ? <span>Sending</span> : <Clock iso={it.at} />}
          {dt && <span className={'dry d-' + it.delivery_state} title={dt}>{DELIVERY_TAG[it.delivery_state || ''] || 'Practice'}</span>}
          {it.delivery_state === 'failed' && <span className="dfail">{dt}</span>}
        </div>
      </div>
    </div>
  )
}

export function Timeline({ tl, contact }: { tl: TL; contact: Row }) {
  const ref = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const anchor = useRef<{ h: number; t: number } | null>(null)
  const first = useRef(true)
  const [fresh, setFresh] = useState(false)
  const chrono = useMemo(() => [...tl.items].reverse(), [tl.items])
  const all = useMemo(() => [...chrono, ...tl.pending], [chrono, tl.pending])
  const topKey = tl.items[0] ? keyOf(tl.items[0]) : ''
  const lastTop = useRef(topKey)

  useEffect(() => { first.current = true; stick.current = true; setFresh(false) }, [contact.id])
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    if (anchor.current) { el.scrollTop = el.scrollHeight - anchor.current.h + anchor.current.t; anchor.current = null; return }
    if (first.current && all.length) { el.scrollTop = el.scrollHeight; first.current = false; lastTop.current = topKey; return }
    if (stick.current) { el.scrollTop = el.scrollHeight; setFresh(false) }
    else if (topKey !== lastTop.current && tl.items[0]) setFresh(true)
    lastTop.current = topKey
  }, [all.length, topKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const older = useCallback(async () => {
    const el = ref.current
    if (el) anchor.current = { h: el.scrollHeight, t: el.scrollTop }
    const did = await tl.loadOlder()
    if (!did) anchor.current = null
  }, [tl])
  function onScroll() {
    const el = ref.current
    if (!el) return
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (stick.current) setFresh(false)
    if (el.scrollTop < 60 && tl.next && !tl.older && !tl.loading) older()
  }
  let lastDay = ''
  return (
    <div className="tl-wrap">
      <div className="tl" ref={ref} onScroll={onScroll} role="log" aria-label={`Conversation with ${contact.display_name}`} aria-live="polite">
        {tl.loading && <div className="tl-sk">{[60, 40, 70, 30].map((w, i) => <div key={i} className={'brow ' + (i % 2 ? 'out' : 'in')}><div className="bb sk-bb"><Skeleton w={`${w}%`} /><Skeleton w="30%" h={11} /></div></div>)}</div>}
        {tl.error && !tl.items.length && <p className="sub pad">Could not load the conversation. It will try again.</p>}
        {tl.next && <div className="tl-more"><button className="btn small" onClick={older} disabled={tl.older}>{tl.older ? 'Loading' : 'Load older'}</button></div>}
        {!tl.loading && !tl.next && tl.items.length > 0 && <div className="tl-start">Start of the conversation</div>}
        {!tl.loading && !tl.error && all.length === 0 && (
          <div className="tl-empty"><Icon n="chat" size={28} /><h3>No messages yet</h3><p>Send {firstName(contact.display_name)} a text or email below, or leave a note for the team.</p></div>
        )}
        {all.map((it) => {
          const dk = dayKey(it.at)
          const head = dk !== lastDay
          lastDay = dk
          return (
            <div key={keyOf(it)}>
              {head && <div className="tl-day"><span>{dayLabel(it.at)}</span></div>}
              <Item it={it} contact={contact} />
            </div>
          )
        })}
      </div>
      {fresh && <button className="fresh" onClick={() => { const el = ref.current; if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); setFresh(false) }}>New activity <Icon n="chevD" size={14} /></button>}
    </div>
  )
}


function CallLine({ it, contact }: { it: TItem; contact: { display_name: string } }) {
  const player = usePlayer()
  const [tr, setTr] = useState<number | null>(null)
  const call = it.meta?.call
  const v = call ? player.st[call.id] : undefined
  return (
    <>
      <div className="callline">
        <span className="sl-ic call"><Icon n="phone" size={15} /></span>
        <div><b>{it.direction === 'in' ? 'Call from ' + firstName(contact.display_name) : 'Call to ' + firstName(contact.display_name)}</b>{it.body && <div className="mute">{it.body}</div>}</div>
        {call && <CallButtons call={call} player={player} onTranscript={setTr} />}
        <Clock iso={it.at} />
      </div>
      {call && v && <table className="callplay"><tbody><PlayRow id={call.id} v={v} player={player} cols={1} /></tbody></table>}
      <TranscriptModal callId={tr} onClose={() => setTr(null)} />
    </>
  )
}


/** "Correct this" on a reply the assistant sent: the right answer is saved as knowledge (approved at once when an owner saves it). */
function CorrectButton({ messageId, reply }: { messageId: number; reply: string }) {
  const { can, toast } = useApp()
  const [open, setOpen] = useState(false)
  const [answer, setAnswer] = useState('')
  const [title, setTitle] = useState('')
  const [cat, setCat] = useState('patient_communication')
  const [busy, setBusy] = useState(false)
  if (!can('knowledge.suggest')) return null
  async function save() {
    setBusy(true)
    try {
      const r = await api<{ item: { status: string } }>(`/messages/${messageId}/correct`, { method: 'POST', body: { answer, title: title.trim() || null, category: cat }, quiet: true })
      toast(r.item.status === 'approved' ? 'Saved. The assistant will use this from now on.' : 'Saved for the owner to approve.')
      setOpen(false)
    } catch (x) { toast(x instanceof ApiError && x.message && x.message !== 'error' ? x.message : 'Not saved.') }
    setBusy(false)
  }
  return (
    <>
      <button className="link-a correct-b" onClick={() => { setAnswer(''); setTitle(''); setOpen(true) }}>Correct this</button>
      <Modal open={open} onClose={() => setOpen(false)} title="Correct the assistant" footer={<><button className="btn" onClick={() => setOpen(false)}>Cancel</button><button className="btn primary" disabled={busy || answer.trim().length < 2} onClick={save}>Save answer</button></>}>
        <p className="small mute">It said:</p>
        <p className="quote">{reply}</p>
        <div className="form">
          <label>What should it answer instead?<textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={4} maxLength={2000} placeholder="Write the answer the way you would text it. It is used word for word." data-autofocus /></label>
          <label>Short title (optional)<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="For example: Bird nail trims" /></label>
          <label>Kind of answer<select value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="clinic_info">Clinic info</option><option value="fees">Fees</option><option value="patient_knowledge">Patient knowledge</option>
            <option value="patient_communication">Patient communication</option><option value="inquiry_communication">Inquiry communication</option><option value="specials">Specials</option></select></label>
        </div>
        <p className="note">Saved in Knowledge & Fees, so you never have to say it twice. Never put medical advice or doses here.</p>
      </Modal>
    </>
  )
}
