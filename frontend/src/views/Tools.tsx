import { useEffect, useRef, useState } from 'react'
import { useLive } from '../live'
import { Csv, Head } from '../components/bits'

type Ev = { id: number; t_display: string; tool: string; text: string }
type Tool = { name: string; who: string; writes: string; event_count: number }

export function Tools() {
  const tools = useLive<{ items: Tool[] }>('/tools', ['events'])
  const feed = useLive<{ items: Ev[] }>('/events?limit=12', ['events'])
  const seen = useRef<Set<number> | null>(null)
  const [fresh, setFresh] = useState<Set<number>>(new Set())
  useEffect(() => {
    const ids = (feed.data?.items || []).map((e) => e.id)
    if (!feed.data) return
    if (seen.current === null) { seen.current = new Set(ids); return }
    const nw = ids.filter((i) => !seen.current!.has(i))
    ids.forEach((i) => seen.current!.add(i))
    if (nw.length) { setFresh(new Set(nw)); const t = setTimeout(() => setFresh(new Set()), 1800); return () => clearTimeout(t) }
  }, [feed.data])
  return (
    <>
      <Head title="How it fits together" lead="Small tools help staff do their jobs and write what happens into one shared record. The assistant reads that record and reports to Dr. Garg." right={<Csv name="events" />} />
      <section className="card">
        <div className="org">
          <div className="senior"><b>Assistant</b> reports to Dr. Garg<br /><small style={{ color: 'var(--mute)' }}>Reads the shared record, writes the daily brief, suggests changes, acts only after approval.</small></div>
          <div className="link-line" />
          <div className="juniors">
            {(tools.data?.items || []).map((t) => (
              <div className="junior" key={t.name}>
                <b><span><span className="dot" />{t.name}</span><span className="tag">{t.event_count} {t.event_count === 1 ? 'event' : 'events'}</span></b>
                <small>Used by: {t.who}</small>
                <small>Writes: {t.writes}</small>
              </div>
            ))}
          </div>
        </div>
      </section>
      <section className="card">
        <h2>Shared clinic record, live</h2>
        <p className="sub">Try approving a message or marking a callback done. It appears here.</p>
        <ul className="feed" aria-live="polite">
          {(feed.data?.items || []).map((e) => (
            <li key={e.id} className={fresh.has(e.id) ? 'fresh' : ''}><span className="t mono">{e.t_display}</span><span className="tool">{e.tool}</span><span>{e.text}</span></li>
          ))}
        </ul>
      </section>
    </>
  )
}
