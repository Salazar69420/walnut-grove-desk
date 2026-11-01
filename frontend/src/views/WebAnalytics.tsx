import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { Empty, Segmented, SkeletonRows } from '../components/ui'
import { Icon } from '../components/icons'

type Row = { name: string; pageviews: number; visitors: number }
type Day = { day: string; pageviews: number; visitors: number; known: boolean }
type Data = {
  connected: boolean; days: number; since: string; until: string; site: string
  totals: { pageviews: number; visitors: number; pages_per_visitor: number }; previous: { pageviews: number; visitors: number } | null
  series: Day[]; top: Record<'pages' | 'referrers' | 'countries' | 'devices' | 'browsers' | 'systems', Row[]>; top_since: string
  inbound_calls: number; seo: { at: string; score: number; areas: Record<string, number> | null }[]; collecting: boolean; limits: { window_days: number; plan_note: string }
}
const RANGES: ['7' | '30' | '90', string][] = [['7', '7 days'], ['30', '30 days'], ['90', '90 days']]
const n0 = (n: number) => n.toLocaleString()
const dayLabel = (d: string, long = false) => new Date(d + 'T12:00:00Z').toLocaleDateString([], long ? { weekday: 'short', month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric' })
let regions: Intl.DisplayNames | null = null
try { regions = new Intl.DisplayNames(['en'], { type: 'region' }) } catch { regions = null }
const country = (c: string) => { try { return (c.length === 2 && regions?.of(c.toUpperCase())) || c } catch { return c } }
const DEVICE: Record<string, string> = { desktop: 'Computer', mobile: 'Phone', tablet: 'Tablet' }

/** Website analytics: who visits the clinic website, which pages, from where, on what (Vercel Web Analytics), and the SEO score over time. */
export function WebAnalytics() {
  const [range, setRange] = useState<'7' | '30' | '90'>(() => { try { return (localStorage.getItem('wgd.wa.range') as '7' | '30' | '90') || '30' } catch { return '30' } })
  const [d, setD] = useState<Data | null>(null)
  const [err, setErr] = useState('')
  const [table, setTable] = useState(false)
  useEffect(() => {
    let live = true
    setD(null); setErr('')
    try { localStorage.setItem('wgd.wa.range', range) } catch { /* */ }
    api<Data>(`/website/analytics?days=${range}`, { quiet: true }).then((r) => { if (live) setD(r) })
      .catch((x) => { if (live) setErr(x instanceof ApiError && x.message !== 'error' ? x.message : 'Could not load the analytics.') })
    return () => { live = false }
  }, [range])

  return (
    <div className="php web wa">
      <div className="hd2">
        <div><h1>Website analytics</h1><p className="lead">Who visits {d?.site || 'the clinic website'}, which pages they read and where they come from. From Vercel Web Analytics: no cookies, no personal data.</p></div>
        <Segmented<'7' | '30' | '90'> label="Period" value={range} onChange={setRange} options={RANGES} />
      </div>
      {err ? <div className="card pad"><p className="bad">{err}</p></div>
        : !d ? <div className="card pad"><SkeletonRows n={5} /></div>
        : !d.connected ? <div className="card pad"><Empty icon="eye" title="Connect the website first" text="Website analytics reads from the site's Vercel project. Connect it once in SEO agent, Deployment agent." action={<Link className="btn primary" to="/website/seo">Open SEO agent</Link>} /></div>
        : <Body d={d} table={table} setTable={setTable} />}
    </div>
  )
}

function Delta({ now, before }: { now: number; before?: number }) {
  if (before === undefined) return null
  if (!before) return now ? <span className="wa-d up">new</span> : null
  const pct = Math.round(((now - before) / before) * 100)
  if (pct === 0) return <span className="wa-d">same as before</span>
  return <span className={'wa-d ' + (pct > 0 ? 'up' : 'down')}>{pct > 0 ? '▲' : '▼'} {Math.abs(pct)}% vs the period before</span>
}

function Body({ d, table, setTable }: { d: Data; table: boolean; setTable: (b: boolean) => void }) {
  const p = d.previous || undefined
  return (
    <>
      {!d.collecting && (
        <div className="card pad wa-note">
          <Icon n="alert" size={18} />
          <div><b>No visits recorded yet.</b> Vercel Web Analytics is switched on, but the website only starts sending visits once the tracking change is live: approve the change "Turns on visitor statistics" in <Link to="/website/seo">SEO agent, Deployment agent</Link>. Numbers appear here within minutes of the first visitors.</div>
        </div>
      )}
      <div className="wa-kpis">
        <div className="card wa-kpi"><span>Visitors</span><b>{n0(d.totals.visitors)}</b><Delta now={d.totals.visitors} before={p?.visitors} /></div>
        <div className="card wa-kpi"><span>Page views</span><b>{n0(d.totals.pageviews)}</b><Delta now={d.totals.pageviews} before={p?.pageviews} /></div>
        <div className="card wa-kpi"><span>Pages per visitor</span><b>{d.totals.pages_per_visitor ? d.totals.pages_per_visitor.toFixed(1) : '0'}</b><span className="wa-d">how far people read</span></div>
        <div className="card wa-kpi"><span>Calls in the same days</span><b>{n0(d.inbound_calls)}</b><Link className="wa-d" to="/phone">inbound, from the phone system</Link></div>
      </div>

      <section className="card pad">
        <div className="wa-h"><h3>Visitors per day</h3><button className="linkbtn" onClick={() => setTable(!table)}>{table ? 'Show the chart' : 'Show as a table'}</button></div>
        {table ? <DayTable s={d.series} /> : <DayChart s={d.series} />}
        {d.days > d.limits.window_days && <p className="mute small">Vercel's free plan keeps {d.limits.window_days} days; older days come from the copy this app keeps (it started when the analytics were connected).</p>}
      </section>

      <div className="wa-grid">
        <TopList title="Most visited pages" rows={d.top.pages} link={(n) => `https://${d.site}${n}`} />
        <TopList title="Where visitors come from" rows={d.top.referrers} hint="Search engines, social media and other sites that linked here. Direct means typed in, a bookmark, or an app." />
        <TopList title="Countries" rows={d.top.countries} label={country} />
        <TopList title="Devices" rows={d.top.devices} label={(x) => DEVICE[x.toLowerCase()] || x} />
        <TopList title="Browsers" rows={d.top.browsers} />
        <TopList title="Operating systems" rows={d.top.systems} />
      </div>
      {d.days > d.limits.window_days && <p className="mute small">Page, source, country and device lists cover {dayLabel(d.top_since)} to today (what Vercel keeps on the free plan).</p>}

      <section className="card pad">
        <div className="wa-h"><h3>SEO score over time</h3><Link className="linkbtn" to="/website/seo">Open SEO agent</Link></div>
        {d.seo.length === 0 ? <p className="mute">No audits yet.</p> : <SeoTrend seo={d.seo} />}
      </section>
      <p className="mute small">{d.limits.plan_note}</p>
    </>
  )
}

function DayChart({ s }: { s: Day[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...s.map((x) => x.visitors))
  const nice = useMemo(() => { const p = Math.pow(10, Math.floor(Math.log10(max))); return Math.ceil(max / p) * p }, [max])
  const every = Math.max(1, Math.ceil(s.length / 8))
  const h = hover !== null ? s[hover] : null
  return (
    <div className="wa-chart" onMouseLeave={() => setHover(null)}>
      <div className="wa-y" aria-hidden="true"><span>{n0(nice)}</span><span>{n0(Math.round(nice / 2))}</span><span>0</span></div>
      <div className="wa-plot">
        <div className="wa-grid-l" aria-hidden="true"><i /><i /><i /></div>
        <div className="wa-bars" role="list" aria-label="Visitors per day">
          {s.map((x, i) => (
            <button key={x.day} role="listitem" className={'wa-bar' + (x.known ? '' : ' unk') + (hover === i ? ' on' : '')} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
              aria-label={`${dayLabel(x.day, true)}: ${x.known ? `${x.visitors} visitors, ${x.pageviews} page views` : 'no data'}`}>
              <span style={{ height: `${(x.visitors / nice) * 100}%` }} />
            </button>
          ))}
        </div>
        <div className="wa-x" aria-hidden="true">{s.map((x, i) => <span key={x.day}>{i % every === 0 ? dayLabel(x.day) : ''}</span>)}</div>
        {h && <div className="wa-tip" style={{ left: `${((hover! + 0.5) / s.length) * 100}%` }} role="status">
          <b>{dayLabel(h.day, true)}</b>{h.known ? <><span>{n0(h.visitors)} visitors</span><span>{n0(h.pageviews)} page views</span></> : <span>No data for this day</span>}
        </div>}
      </div>
    </div>
  )
}

function DayTable({ s }: { s: Day[] }) {
  return (
    <div className="wa-tw"><table className="tbl"><thead><tr><th>Day</th><th className="num">Visitors</th><th className="num">Page views</th></tr></thead>
      <tbody>{[...s].reverse().map((x) => <tr key={x.day}><td>{dayLabel(x.day, true)}</td><td className="num">{x.known ? n0(x.visitors) : '-'}</td><td className="num">{x.known ? n0(x.pageviews) : '-'}</td></tr>)}</tbody></table></div>
  )
}

function TopList({ title, rows, label = (x) => x, link, hint }: { title: string; rows: Row[]; label?: (x: string) => string; link?: (x: string) => string; hint?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.visitors))
  return (
    <section className="card pad wa-top">
      <div className="wa-h"><h3>{title}</h3><span className="mute small">visitors</span></div>
      {hint && <p className="mute small" style={{ marginTop: 0 }}>{hint}</p>}
      {rows.length === 0 ? <p className="mute">Nothing yet.</p> : (
        <ul>{rows.map((r) => (
          <li key={r.name} title={`${r.visitors} visitors, ${r.pageviews} page views`}>
            <span className="wa-fill" style={{ width: `${(r.visitors / max) * 100}%` }} aria-hidden="true" />
            <span className="wa-name">{link && r.name.startsWith('/') ? <a href={link(r.name)} target="_blank" rel="noreferrer">{label(r.name)}</a> : label(r.name)}</span>
            <span className="wa-val">{n0(r.visitors)}</span>
          </li>))}
        </ul>
      )}
    </section>
  )
}

function SeoTrend({ seo }: { seo: Data['seo'] }) {
  const last = seo[seo.length - 1]
  return (
    <div className="wa-seo">
      <div className="wa-kpi plain"><span>Latest score</span><b>{last.score}<small>/100</small></b>
        {last.areas && <span className="wa-d">Google {last.areas.google} · Maps {last.areas.maps} · AI search {last.areas.ai}</span>}</div>
      <ol className="wa-steps">{seo.map((a, i) => (
        <li key={i}><span className="wa-sbar"><span style={{ height: `${a.score}%` }} /></span><b>{a.score}</b><small>{new Date(a.at).toLocaleDateString([], { month: 'short', day: 'numeric' })}</small></li>
      ))}</ol>
    </div>
  )
}
