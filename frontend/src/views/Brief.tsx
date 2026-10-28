import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Loading } from '../components/bits'

type Chart = { mode: 'real' | 'example'; available_modes: string[]; hours: string[]; calls: number[]; cover: number[]; per_person?: number[]; limit: number; calls_label: string; cover_label: string }
type Sug = { id: number; rule_key: string; title: string; why: string; status: string }
type Brief = {
  greeting: string; date_line: string
  kpis: { key: string; value: number | string; label: string; alert: boolean }[]
  overnight: { total: number; booking: number; callback: number; emergency: number; pending: number }
  suggestions: Sug[]
  chart: Chart
  text: { source: string; paragraph: string }
}

const TOASTS: Record<string, string> = {
  desk_shift: 'Added to the schedule', missed_texts: 'Missed-call texts on (mock)',
  recall_batch: 'Drafts sent to the front desk', old_results: 'Nurses can now call these clients',
}

export function ChartSvg({ c }: { c: Chart }) {
  const W = 640, H = 230, L = 34, B = 28, T = 16, R = 8
  const pw = W - L - R, ph = H - B - T
  const n = c.hours.length || 1, bw = pw / n
  const vals = c.hours.map((_, i) => c.per_person ? c.per_person[i] : c.calls[i] / Math.max(c.cover[i] || 1, 1))
  const top = Math.max(24, ...vals, c.limit)
  const max = Math.ceil(top / 8) * 8
  const ticks = [0, max / 3, (max * 2) / 3, max].map((v) => Math.round(v))
  const ly = T + ph - (c.limit / max) * ph
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Calls per person on the phones by hour">
      {ticks.map((v) => {
        const y = T + ph - (v / max) * ph
        return <g key={v}><line x1={L} x2={W - R} y1={y} y2={y} stroke="var(--line)" strokeWidth={1} /><text x={L - 6} y={y + 4} textAnchor="end">{v}</text></g>
      })}
      {vals.map((v, i) => {
        const h = (v / max) * ph, x = L + i * bw + bw * 0.18, w = bw * 0.64, y = T + ph - h, hot = v > c.limit
        return (
          <g key={i}>
            <rect x={x} y={y} width={w} height={h} rx={3} fill={hot ? 'var(--warn)' : 'var(--bar)'} />
            <text className="val" x={x + w / 2} y={y - 4} textAnchor="middle">{Math.round(v * 10) / 10}</text>
            <text x={x + w / 2} y={H - 10} textAnchor="middle">{c.hours[i]}</text>
          </g>
        )
      })}
      <line x1={L} x2={W - R} y1={ly} y2={ly} stroke="var(--crit)" strokeWidth={1.5} strokeDasharray="5 4" />
      <text x={W - R - 2} y={ly - 5} textAnchor="end" style={{ fill: 'var(--crit)' }}>Stretched above {c.limit} calls an hour per person</text>
    </svg>
  )
}

export function Brief() {
  const { toast, run, busy } = useApp()
  const nav = useNavigate()
  const [mode, setMode] = useState<string>('')
  const b = useLive<Brief>('/brief' + (mode ? '?chart=' + mode : ''), ['suggestions', 'callbacks', 'results', 'shifts', 'overnight', 'analytics', 'settings'])
  const d = b.data
  const decide = (s: Sug, yes: boolean) => run('sg' + s.id, async () => {
    await api(`/suggestions/${s.id}/${yes ? 'approve' : 'dismiss'}`, { method: 'POST' })
    toast(yes ? TOASTS[s.rule_key] || 'Approved' : 'Dismissed'); b.reload()
  })
  if (!d) return <Loading loading={b.loading} error={b.error} />
  const o = d.overnight
  return (
    <>
      <div><h1>{d.greeting}</h1><p className="lead">{d.date_line}</p></div>
      {d.text?.paragraph && <section className="card"><p style={{ margin: 0 }}>{d.text.paragraph}</p></section>}
      <div className="kpis">
        {d.kpis.map((k) => <div key={k.key} className={'kpi' + (k.alert ? ' alert' : '')}><div className="n mono">{k.value}</div><div className="l">{k.label}</div></div>)}
      </div>
      <section className="card">
        <div className="hd2">
          <div>
            <h2>Overnight, since 6 pm</h2>
            <p className="sub" style={{ margin: 0 }}>The after-hours assistant handled {o.total} conversations: {o.booking} appointment requests, {o.callback} for callbacks, and {o.emergency} sent to an emergency hospital.</p>
          </div>
          <button className="btn small" onClick={() => nav('/overnight')}>Open overnight</button>
        </div>
      </section>
      <section className="card">
        <h2>Needs your decision</h2>
        <p className="sub">Suggestions from the assistant. Nothing happens until you approve it.</p>
        {d.suggestions.length === 0 && <p className="sub">Nothing needs a decision.</p>}
        {d.suggestions.map((s) => (
          <div className="rec" key={s.id} data-sug={s.rule_key}>
            <div><b>{s.title}</b><p>{s.why}</p></div>
            <div className="acts" style={{ flexDirection: 'row' }}>
              {s.status === 'open' ? (
                <>
                  <button className="btn primary small" disabled={busy.has('sg' + s.id)} onClick={() => decide(s, true)}>Approve</button>
                  <button className="btn small" disabled={busy.has('sg' + s.id)} onClick={() => decide(s, false)}>Not now</button>
                </>
              ) : s.status === 'approved' ? <span className="tag ok">Approved</span> : <span className="tag">Dismissed</span>}
            </div>
          </div>
        ))}
      </section>
      <section className="card">
        <div className="hd2">
          <div>
            <h2>Calls per person on the phones, typical weekday</h2>
            <p className="sub">Bars above the line mean the desk is stretched. Approve the extra shift above and watch the first two bars drop.</p>
          </div>
          {d.chart.available_modes.length > 1 && (
            <div className="seg" role="group" aria-label="Chart data">
              <button aria-pressed={d.chart.mode === 'real'} onClick={() => setMode('real')}>Real recorded volume</button>
              <button aria-pressed={d.chart.mode === 'example'} onClick={() => setMode('example')}>Example numbers</button>
            </div>
          )}
        </div>
        <ChartSvg c={d.chart} />
        <p className="note">{d.chart.calls_label}</p>
        <p className="note" style={{ marginTop: 2 }}>{d.chart.cover_label}</p>
      </section>
    </>
  )
}
