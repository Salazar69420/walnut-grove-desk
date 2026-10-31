import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Avatar, Empty, KindBadge, Segmented, SkeletonRows, Switch, Time, useMedia } from '../components/ui'
import { Icon } from '../components/icons'
import { clock, dayKey, dayLabel, hueFor, ls, lsSet } from '../lib/fmt'
import type { Ev, Row, Task } from '../lib/types'

type HomeData = { people: Row[]; events: Ev[]; tasks: Task[]; counts: { unread_people: number; events_today: number; open_tasks: number; my_tasks: number } }
type Tab = 'people' | 'cal' | 'tasks'

export function Home() {
  const { can } = useApp()
  const nav = useNavigate()
  const [mine, setMine] = useState(() => ls('wgd-home-mine') === '1')
  const [tab, setTab] = useState<Tab>('people')
  const tabs = useMedia('(max-width: 900px)')
  const h = useLive<HomeData>('/home?mine=' + (mine ? 1 : 0), ['contacts', 'messages', 'tasks', 'events_cal'])
  const d = h.data
  const setM = (v: boolean) => { setMine(v); lsSet('wgd-home-mine', v ? '1' : '0') }

  const people = (
    <Col title="Inquiries and patients" count={d?.counts.unread_people} countLabel="unread" id="people">
      {!d ? <SkeletonRows n={7} /> : d.people.length === 0 ? (
        <Empty icon="people" title={mine ? 'None of your people yet' : 'No contacts yet'} text={mine ? 'Turn off "Assigned to me only" to see everyone.' : 'Add the first contact to get started.'}
          action={can('contacts.edit') ? <button className="btn primary" onClick={() => nav('/contacts?add=1')}>Add contact</button> : undefined} />
      ) : d.people.map((p) => <PersonItem key={p.id} p={p} />)}
    </Col>
  )
  const cal = (
    <Col title="Calendar" count={d?.counts.events_today} countLabel="today" id="cal">
      {!d ? <SkeletonRows n={6} h={56} /> : <Agenda events={d.events} mine={mine} />}
    </Col>
  )
  const tasks = (
    <Col title="Tasks" count={d?.counts.open_tasks} countLabel="open" id="tasks" more={<Link to="/tasks" className="link-a">All tasks</Link>}>
      {!d ? <SkeletonRows n={6} h={64} /> : d.tasks.length === 0 ? (
        <Empty icon="check" title="You are all caught up" text={mine ? 'No open tasks are assigned to you.' : 'There are no open tasks.'} />
      ) : d.tasks.map((t) => <TaskItem key={t.id} t={t} />)}
    </Col>
  )

  return (
    <>
      <div className="hd2">
        <div><h1>Home</h1><p className="lead">Who wrote in, what is on today, and what needs doing.</p></div>
        <div className="homebar">
          <label className="mineSw"><Switch checked={mine} onChange={setM} label="Assigned to me only" /><span>Assigned to me only</span></label>
          {can('contacts.edit') && <button className="btn primary" onClick={() => nav('/contacts?add=1')}><Icon n="plus" size={16} /> Add contact</button>}
        </div>
      </div>
      {d && (
        <div className="stats" aria-label="Counts">
          <Stat n={d.counts.unread_people} l={d.counts.unread_people === 1 ? 'person waiting' : 'people waiting'} hot={d.counts.unread_people > 0} />
          <Stat n={d.counts.events_today} l={d.counts.events_today === 1 ? 'event today' : 'events today'} />
          <Stat n={d.counts.open_tasks} l={d.counts.open_tasks === 1 ? 'open task' : 'open tasks'} />
          <Stat n={d.counts.my_tasks} l={d.counts.my_tasks === 1 ? 'task is yours' : 'tasks are yours'} />
        </div>
      )}
      {h.error && !d && <p className="sub">Could not load Home. It will try again.</p>}
      {tabs ? (
        <>
          <Segmented<Tab> label="Home sections" value={tab} onChange={setTab} options={[['people', 'People'], ['cal', 'Calendar'], ['tasks', 'Tasks']]} />
          {tab === 'people' ? people : tab === 'cal' ? cal : tasks}
        </>
      ) : <div className="home3">{people}{cal}{tasks}</div>}
    </>
  )
}

function Stat({ n, l, hot }: { n: number; l: string; hot?: boolean }) {
  return <div className={'stat' + (hot ? ' hot' : '')}><b>{n}</b><span>{l}</span></div>
}
function Col({ title, count, countLabel, children, more, id }: { title: string; count?: number; countLabel: string; children: React.ReactNode; more?: React.ReactNode; id: string }) {
  return (
    <section className="card col" aria-labelledby={'col-' + id}>
      <header className="col-h"><h2 id={'col-' + id}>{title}</h2>{count !== undefined && <span className="count-pill">{count} {countLabel}</span>}<span className="spacer" />{more}</header>
      <div className="col-b">{children}</div>
    </section>
  )
}

function PersonItem({ p }: { p: Row }) {
  const lm = p.last_message
  return (
    <Link to={`/contacts/${p.id}`} className={'pitem' + (p.unread ? ' unread' : '')}>
      <Avatar name={p.display_name} initials={p.initials} hue={p.avatar_hue} size={40} />
      <div className="pi-m">
        <div className="pi-n"><b>{p.display_name}</b><KindBadge kind={p.kind} /></div>
        <div className="pi-p">{lm ? <><Icon n={lm.channel === 'email' ? 'mail' : lm.channel === 'call' ? 'phone' : 'chat'} size={13} /> {lm.direction === 'out' ? 'You: ' : ''}{lm.preview}</> : <span className="mute">No messages yet</span>}</div>
      </div>
      <div className="pi-r"><Time iso={lm?.at || p.last_active_at} className="mute" />{p.unread > 0 && <span className="ubdg" aria-label={`${p.unread} unread`}>{p.unread}</span>}</div>
    </Link>
  )
}

function Agenda({ events, mine }: { events: Ev[]; mine: boolean }) {
  const days = useMemo(() => {
    const m = new Map<string, Ev[]>()
    const today = dayKey(Date.now())
    m.set(today, [])
    ;[...events].sort((a, b) => a.starts_at.localeCompare(b.starts_at)).forEach((e) => {
      const k = dayKey(e.starts_at)
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(e)
    })
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [events])
  if (events.length === 0) return <Empty icon="cal" title="Nothing booked this week" text={mine ? 'No events are assigned to you.' : 'New bookings show up here.'} />
  return (
    <>
      {days.map(([k, list]) => (
        <div key={k} className="day">
          <div className="day-h">{dayLabel(list[0]?.starts_at || k + 'T12:00:00Z')}</div>
          {list.length === 0 && <p className="mute day-e">No events</p>}
          {list.map((e) => <EventItem key={e.id} e={e} />)}
        </div>
      ))}
    </>
  )
}
function EventItem({ e }: { e: Ev }) {
  const inner = (
    <>
      <span className="ev-t">{clock(e.starts_at)}</span>
      <span className="ev-m">
        <b>{e.title}</b>
        <span className="mute">{e.kind_label}{e.contact ? ` · ${e.contact.display_name}` : ''}{e.pet ? ` (${e.pet.name})` : ''}{e.staff ? ` · ${e.staff.display_name}` : ''}</span>
      </span>
      {e.status !== 'booked' && <span className={'tag ' + (e.status === 'cancelled' ? 'high' : e.status === 'done' ? 'ok' : 'warn')}>{e.status === 'requested' ? 'Requested' : e.status === 'cancelled' ? 'Cancelled' : 'Done'}</span>}
    </>
  )
  const cls = 'evitem k-' + e.kind + (e.status === 'cancelled' ? ' off' : '')
  return e.contact ? <Link to={`/contacts/${e.contact.id}?tab=calendar`} className={cls}>{inner}</Link> : <div className={cls}>{inner}</div>
}
function TaskItem({ t }: { t: Task }) {
  return (
    <Link to={`/tasks?open=${t.id}`} className={'titem p-' + t.priority}>
      <div className="ti-m">
        <b>{t.title}</b>
        <div className="ti-s"><span className="tag">{t.kind_label}</span>{t.raised_by === 'ai' && <span className="tag acc">AI</span>}{t.priority === 'high' && <span className="tag high">High</span>}{t.contact && <span className="mute">{t.contact.display_name}</span>}</div>
      </div>
      <div className="ti-r">{t.assigned ? <Avatar name={t.assigned.display_name} initials={t.assigned.initials} hue={hueFor(t.assigned.id)} size={28} /> : <span className="tag warn">Unassigned</span>}</div>
    </Link>
  )
}
