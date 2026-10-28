import { FormEvent, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Csv, Head, Loading } from '../components/bits'

type Item = { id: number; client_label: string; pet_label: string; channel: string; at_display: string; kind: string; status: string; preferred_slot: string; summary: string; messages: { who: string; text: string }[]; is_test: boolean }
type Sim = { id: number; channel: 'text' | 'call'; messages: { who: 'bot' | 'client'; text: string }[]; options: { index: number; label: string }[]; done: boolean; accepts_text?: boolean }

export function Overnight() {
  const { can, toast, run, busy } = useApp()
  const nav = useNavigate()
  const ov = useLive<{ items: Item[] }>('/overnight', ['overnight'])
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const items = ov.data?.items || []
  const em = items.filter((o) => o.kind === 'emergency' && o.status === 'new').length
  const manage = can('overnight.manage')
  const act = (key: string, path: string, msg: string) => run(key, async () => { await api(path, { method: 'POST' }); toast(msg); ov.reload() })

  return (
    <>
      <Head title="Overnight" lead="Every call and text that came in after closing, already sorted by the assistant. Confirm the bookings, check the emergencies, and the rest is waiting in callbacks."
        right={<div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}><Csv name="overnight" /><button className="btn" onClick={() => document.getElementById('sim')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Try it as a client</button></div>} />
      {em > 0 && <div className="banner" role="alert">{em} {em === 1 ? 'conversation was' : 'conversations were'} sent to an emergency hospital. Call these clients first.</div>}
      <section className="card">
        <Loading loading={ov.loading && !ov.data} error={ov.error && !ov.data} />
        {items.map((o) => {
          const kt = o.kind === 'booking' ? <span className="tag acc">Appointment request</span> : o.kind === 'emergency' ? <span className="tag high">Sent to emergency hospital</span> : <span className="tag">Added to callbacks</span>
          let acts: JSX.Element | null = null
          if (o.kind === 'booking') {
            acts = o.status === 'new' ? (manage ? <>
              <button className="btn primary small" disabled={busy.has('c' + o.id)} onClick={() => act('c' + o.id, `/overnight/${o.id}/confirm`, 'Confirmation text sent (mock). Now enter it in ImproMed.')}>Confirm {o.preferred_slot}</button>
              <button className="btn small" disabled={busy.has('t' + o.id)} onClick={() => act('t' + o.id, `/overnight/${o.id}/to-callback`, 'Moved to callbacks')}>Call to arrange</button></> : null)
              : o.status === 'confirmed' ? <>
                <span className="tag ok">Confirmed by text (mock)</span>
                {manage && <button className="btn small" disabled={busy.has('e' + o.id)} onClick={() => act('e' + o.id, `/overnight/${o.id}/entered`, 'Marked as entered in ImproMed')}>Done in ImproMed</button>}</>
              : o.status === 'entered' ? <span className="tag ok">In ImproMed</span> : <span className="tag">Moved to callbacks</span>
          } else if (o.kind === 'emergency') {
            acts = o.status === 'new' ? (can('overnight.followup') ? <button className="btn primary small" disabled={busy.has('f' + o.id)} onClick={() => act('f' + o.id, `/overnight/${o.id}/followed-up`, 'Marked followed up')}>Mark followed up</button> : null) : <span className="tag ok">Followed up</span>
          } else {
            acts = o.status === 'new' ? <>
              {manage && <button className="btn small" disabled={busy.has('a' + o.id)} onClick={() => act('a' + o.id, `/overnight/${o.id}/ack`, 'Noted')}>Got it</button>}
              <button className="btn small" onClick={() => nav('/callbacks')}>Open callbacks</button></> : <span className="tag ok">Seen</span>
          }
          return (
            <div key={o.id} className={'row' + (o.status !== 'new' ? ' done' : '')} data-ov={o.id}>
              <div>
                <div className="who">{o.client_label} <span style={{ fontWeight: 400, color: 'var(--mute)' }}>for {o.pet_label}</span></div>
                <div className="what">{o.summary}</div>
                <div className="meta">{kt}<span className="tag">{o.channel}, {o.at_display}</span>{o.is_test && <span className="tag warn">From your test</span>}
                  <button className="link" onClick={() => setOpen({ ...open, [o.id]: !open[o.id] })}>{open[o.id] ? 'Hide conversation' : 'Show conversation'}</button></div>
                {open[o.id] && <div className="conv">{o.messages.map((m, i) => <div key={i}><b>{m.who === 'bot' ? 'Assistant' : 'Client'}:</b> {m.text}</div>)}</div>}
              </div>
              <div className="acts">{acts}</div>
            </div>
          )
        })}
        <p className="note">Confirming a booking texts the client. For now someone also enters it in ImproMed, because there is no connection to its schedule yet.</p>
      </section>
      <Simulator />
    </>
  )
}

function Simulator() {
  const { run, busy } = useApp()
  const [chan, setChan] = useState<'text' | 'call'>('text')
  const [sim, setSim] = useState<Sim | null>(null)
  const [typing, setTyping] = useState(false)
  const [text, setText] = useState('')
  const [shown, setShown] = useState<{ who: string; text: string }[]>([])
  const bub = useRef<HTMLDivElement>(null)

  async function start(c: 'text' | 'call') {
    setChan(c); setSim(null); setShown([])
    await run('simstart', async () => {
      const r = await api<{ session: Sim }>('/sim/start', { body: { channel: c } })
      setSim(r.session); setShown(r.session.messages)
    })
  }
  useEffect(() => { start('text') /* eslint-disable-next-line */ }, [])
  useEffect(() => { bub.current?.scrollTo({ top: bub.current.scrollHeight }) }, [shown, typing])

  async function send(body: { option_index?: number; text?: string }, label: string) {
    if (!sim || typing) return
    setShown((s) => [...s, { who: 'client', text: label }])
    setTyping(true)
    const wait = new Promise((r) => setTimeout(r, 650))
    await run('simreply', async () => {
      try {
        const [r] = await Promise.all([api<{ session: Sim }>(`/sim/${sim.id}/reply`, { body }), wait])
        setSim(r.session); setShown(r.session.messages)
      } catch { setShown(sim.messages) }
    })
    setTyping(false)
  }
  function submit(e: FormEvent) {
    e.preventDefault()
    const t = text.trim()
    if (!t) return
    setText('')
    send({ text: t }, t)
  }
  const call = chan === 'call'
  const done = !!sim?.done
  return (
    <section className="card" id="sim">
      <h2>Try it as a client</h2>
      <p className="sub">This is what a client sees after hours. Tap the answers to play a conversation. What you pick lands in the lists above, the same way a real one would.</p>
      <div className="seg" role="group" aria-label="Channel" style={{ width: 'fit-content', maxWidth: '100%' }}>
        <button aria-pressed={chan === 'text'} disabled={busy.has('simstart')} onClick={() => start('text')}>Text message</button>
        <button aria-pressed={chan === 'call'} disabled={busy.has('simstart')} onClick={() => start('call')}>Phone call</button>
      </div>
      <div className="phone" style={{ maxWidth: 380, marginTop: 12 }}>
        <div className="hd">{call ? 'Phone call, live transcript' : 'Text message'}</div>
        <div className="bubs" ref={bub}>
          {shown.map((m, i) => (
            <div key={i} className={'bub ' + m.who}><small>{call ? (m.who === 'bot' ? 'Assistant, voice' : 'Caller') : (m.who === 'bot' ? 'Clinic assistant' : 'You')}</small>{m.text}</div>
          ))}
          {typing && <div className="bub bot"><span className="typing" aria-label="Typing"><i /><i /><i /></span></div>}
        </div>
        {!typing && !done && sim && (
          <div className="quick">
            {sim.options.map((o) => <button key={o.index} className="btn small" disabled={busy.has('simreply')} onClick={() => send({ option_index: o.index }, o.label)}>{o.label}</button>)}
          </div>
        )}
        {!done && sim && sim.accepts_text !== false && (
          <form className="sim-in" onSubmit={submit}>
            <input value={text} onChange={(e) => setText(e.target.value)} maxLength={500} placeholder="Or type your own message" aria-label="Type your own message" disabled={typing} />
            <button className="btn small" type="submit" disabled={typing || !text.trim()}>Send</button>
          </form>
        )}
      </div>
      {done && <p className="note" style={{ margin: '10px 0 0' }}>This conversation was added to the overnight list above. <button className="link" onClick={() => start(chan)} disabled={busy.has('simstart')}>Start over</button></p>}
      {!done && sim && <p className="note" style={{ margin: '10px 0 0' }}><button className="link" onClick={() => start(chan)} disabled={busy.has('simstart')}>Start over</button></p>}
    </section>
  )
}
