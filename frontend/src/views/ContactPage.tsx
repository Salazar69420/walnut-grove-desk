import { useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useContactDetail, useStaff } from '../lib/store'
import { useMedia, Avatar, Empty, KindBadge, Segmented, Skeleton, Time } from '../components/ui'
import { Icon } from '../components/icons'
import { GuestCard, type CardTab } from '../components/GuestCard'
import { Timeline, useTimeline } from '../components/Timeline'
import { Composer } from '../components/Composer'
import { clock, dayLabel, firstName, ls, lsSet } from '../lib/fmt'
import { VISIT_LABEL } from '../lib/types'

const TABS: CardTab[] = ['contact', 'pets', 'calendar', 'services']
type Ch = 'all' | 'sms' | 'email' | 'call' | 'note'

export function ContactPage() {
  const id = Number(useParams().id)
  const nav = useNavigate()
  const loc = useLocation()
  const [sp, setSp] = useSearchParams()
  const det = useContactDetail(id)
  const stacked = useMedia('(max-width: 1000px)')
  const [view, setView] = useState<'chat' | 'card'>(sp.get('tab') ? 'card' : 'chat')
  const [open, setOpen] = useState(ls('wgd-card-open') !== '0')
  const [ch, setCh] = useState<Ch>('all')
  const tl = useTimeline(id, ch)
  const staff = useStaff()
  void staff
  const t = sp.get('tab') as CardTab | null
  const tab: CardTab = t && TABS.includes(t) ? t : 'contact'
  const setTab = (x: CardTab) => setSp((p) => { const n = new URLSearchParams(p); n.set('tab', x); return n }, { replace: true })
  const c = det.data?.contact

  const summary = useMemo(() => {
    const d = det.data
    if (!d) return ''
    const task = d.open_tasks[0]
    if (task) return task.title
    const ev = d.upcoming_events.find((e) => e.status !== 'cancelled')
    if (ev) return `Next: ${ev.title}, ${dayLabel(ev.starts_at)} at ${clock(ev.starts_at)}`
    if (d.contact.last_message) return d.contact.last_message.preview
    const v = d.visits[0]
    if (v) return `Last visit: ${VISIT_LABEL[v.kind] || v.kind}`
    return 'No messages yet'
  }, [det.data])

  function back() { if (loc.key !== 'default') nav(-1); else nav('/contacts') }
  const editConsent = () => { setTab('contact'); setOpen(true); setView('card') }
  const toggle = () => { const v = !open; setOpen(v); lsSet('wgd-card-open', v ? '1' : '0') }

  if (det.error && !c) {
    return <Empty icon="alert" title={det.error === 404 ? 'We could not find that contact' : 'Could not load this contact'} text={det.error === 404 ? 'They may have been erased, or the link is wrong.' : 'Check your connection and try again.'} action={<Link className="btn primary" to="/contacts">Back to Contacts</Link>} />
  }
  const card = <GuestCard contactId={id} tab={tab} onTab={setTab} />
  return (
    <div className={'cpage' + (!stacked && !open ? ' closed' : '')}>
      <header className="chead">
        <button className="ibtn" onClick={back} aria-label="Back"><Icon n="chevL" /></button>
        {c ? <Avatar name={c.display_name} initials={c.initials} hue={c.avatar_hue} size={44} /> : <Skeleton w={44} h={44} r={999} />}
        <div className="ch-m">
          {c ? <><div className="ch-n"><h1>{c.display_name}</h1><KindBadge kind={c.kind} /></div><div className="ch-s ell" title={summary}>{summary}</div></> : <><Skeleton w={180} h={20} /><Skeleton w={240} h={12} /></>}
        </div>
        <div className="ch-r">
          {c && <span className="ch-own hide-s" title="Owner">{c.owner ? <><Avatar name={c.owner.display_name} size={24} hue={(c.owner.id * 67 + 23) % 360} />{c.owner.display_name}</> : <span className="mute">No owner</span>}</span>}
          {c?.phone
            ? <a className="btn" href={`tel:${c.phone}`} title={`Call ${c.display_name} with your phone or softphone`}><Icon n="phone" size={16} /> Call</a>
            : <span title="No phone number on file" className="calltip"><button className="btn" disabled aria-disabled="true"><Icon n="phone" size={16} /> Call</button></span>}
          {!stacked && <button className="btn" onClick={toggle} aria-expanded={open} aria-controls="gc-side"><Icon n={open ? 'chevR' : 'chevL'} size={16} /> Guest Card</button>}
        </div>
      </header>
      {stacked && <Segmented<'chat' | 'card'> label="Conversation or Guest Card" value={view} onChange={setView} options={[['chat', 'Conversation'], ['card', 'Guest Card']]} />}
      <div className="cbody">
        {(!stacked || view === 'chat') && (
          <section className="conv2" aria-label="Conversation">
            <div className="conv-h">
              <Segmented<Ch> label="Show" value={ch} onChange={setCh} options={[['all', 'All'], ['sms', 'Text'], ['email', 'Email'], ['call', 'Calls'], ['note', 'Notes']]} />
              {c && c.last_active_at && <span className="mute hide-s">Last active <Time iso={c.last_active_at} /></span>}
            </div>
            {c ? <Timeline tl={tl} contact={c} /> : <div className="tl-wrap"><div className="tl"><Skeleton h={60} r={14} /></div></div>}
            {c && <Composer contact={c} tl={tl} onEditConsent={editConsent} />}
          </section>
        )}
        {(stacked ? view === 'card' : open) && <aside className="side-card" id="gc-side" aria-label={`Guest Card for ${c ? firstName(c.display_name) : 'contact'}`}>{card}</aside>}
      </div>
    </div>
  )
}
