import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { onEntity } from '../live'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'

type Answer = { paragraphs: string[]; bullets: string[]; after: string[]; link: { label: string; view: string } | null }
type Proposal = { id: string; summary: string; state?: 'done' | 'cancelled' | 'failed'; result?: string }
type Msg = { who: 'me' | 'bot'; text?: string; answer?: Answer; typing?: boolean; ai?: string; proposals?: Proposal[] }

const VIEWS: Record<string, string> = { home: '/home', tasks: '/tasks', contacts: '/contacts', brief: '/brief', overnight: '/overnight', callbacks: '/callbacks', results: '/results', schedule: '/schedule', setup: '/setup', tools: '/tools' }

export function Assistant() {
  const { toast } = useApp()
  const nav = useNavigate()
  const [greeting, setGreeting] = useState('')
  const [chips, setChips] = useState<{ key: string; question: string }[]>([])
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [waiting, setWaiting] = useState(false)
  const [aiOn, setAiOn] = useState(false)
  const [turns, setTurns] = useState<{ role: 'user' | 'assistant'; content: string }[]>([])
  const box = useRef<HTMLDivElement>(null)

  const loadChips = useCallback(() => api<{ greeting: string; chips: { key: string; question: string }[]; ai?: boolean }>('/assistant/chips', { quiet: true })
    .then((r) => { setGreeting(r.greeting); setChips(r.chips || []); setAiOn(!!r.ai); return !!r.ai })
    .catch(() => false), [])
  useEffect(() => { loadChips() }, [loadChips])
  // an admin may switch AI for staff on or off while this panel is open
  useEffect(() => onEntity('settings', () => { loadChips() }), [loadChips])
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }) }, [msgs])

  async function ask(body: { key?: string; text?: string }, shown: string) {
    if (waiting) return
    setWaiting(true)
    setMsgs((m) => [...m, { who: 'me', text: shown }, { who: 'bot', typing: true }])
    const delay = new Promise((r) => setTimeout(r, 500))
    try {
      const [r] = await Promise.all([api<{ answer: Answer }>('/assistant/ask', { body }), delay])
      setMsgs((m) => [...m.filter((x) => !x.typing), { who: 'bot', answer: r.answer }])
    } catch {
      setMsgs((m) => m.filter((x) => !x.typing))
    }
    setWaiting(false)
  }
  async function chat(t: string) {
    if (waiting) return
    setWaiting(true)
    const next = [...turns, { role: 'user' as const, content: t }].slice(-12)
    setMsgs((m) => [...m, { who: 'me', text: t }, { who: 'bot', typing: true }])
    try {
      const r = await api<{ text: string; proposals: Proposal[] }>('/assistant/chat', { body: { messages: next }, quiet: true })
      setTurns([...next, { role: 'assistant' as const, content: r.text }].slice(-12))
      setMsgs((m) => [...m.filter((x) => !x.typing), { who: 'bot', ai: r.text, proposals: r.proposals }])
    } catch (x) {
      setMsgs((m) => [...m.filter((x) => !x.typing), { who: 'bot', ai: x instanceof ApiError && x.message && x.message !== 'error' ? x.message : 'The AI could not answer just now. The question buttons still work.' }])
    }
    setWaiting(false)
  }
  async function decide(mi: number, p: Proposal, yes: boolean) {
    const mark = (patch: Partial<Proposal>) => setMsgs((m) => m.map((x, i) => i !== mi ? x : { ...x, proposals: x.proposals?.map((y) => y.id === p.id ? { ...y, ...patch } : y) }))
    if (!yes) { api(`/assistant/proposals/${p.id}/cancel`, { method: 'POST', quiet: true }).catch(() => {}); mark({ state: 'cancelled' }); return }
    try { const r = await api<{ done: string }>(`/assistant/proposals/${p.id}/confirm`, { method: 'POST', quiet: true }); mark({ state: 'done', result: r.done }); toast(r.done) }
    catch (x) { mark({ state: 'failed', result: x instanceof ApiError && x.message !== 'error' ? x.message : 'Not done.' }) }
  }
  function submit(e: FormEvent) {
    e.preventDefault()
    const t = q.trim()
    if (!t) return
    setQ('')
    if (aiOn) { chat(t); return }
    // check once more before falling back to the question buttons, in case AI was just switched on
    loadChips().then((on) => (on ? chat(t) : ask({ text: t }, t)))
  }
  return (
    <aside className="assist" aria-label="Assistant">
      <header>
        <b><span className="dot" style={{ margin: 0 }} />Assistant</b>
        <span>{aiOn ? 'Ask anything about the clinic\'s data, or ask for a change (you confirm it). AI answers use OpenRouter.' : 'Reports to Dr. Garg. Answers come from the shared clinic record.'}</span>
      </header>
      <div className="msgs" id="msgs" aria-live="polite" ref={box}>
        {greeting && <div className="msg"><p>{greeting}</p></div>}
        {msgs.map((m, i) => (
          <div key={i} className={'msg' + (m.who === 'me' ? ' me' : '')}>
            {m.typing ? <span className="typing" aria-label="Typing"><i /><i /><i /></span> : m.ai !== undefined ? (
              <>
                <AiText text={m.ai} />
                {(m.proposals || []).map((p) => (
                  <div key={p.id} className={'propose' + (p.state ? ' ' + p.state : '')}>
                    <p><b>Change:</b> {p.summary}</p>
                    {p.state ? <p className="small">{p.state === 'done' ? 'Done. ' : p.state === 'cancelled' ? 'Cancelled.' : 'Not done. '}{p.result}</p>
                      : <div className="rowbtn"><button className="btn small primary" onClick={() => decide(i, p, true)}>Confirm</button><button className="btn small ghost" onClick={() => decide(i, p, false)}>Cancel</button></div>}
                  </div>
                ))}
              </>
            ) : m.answer ? (
              <>
                {m.answer.paragraphs.map((p, j) => <p key={'p' + j}>{p}</p>)}
                {m.answer.bullets.length > 0 && <ul>{m.answer.bullets.map((b, j) => <li key={j}>{b}</li>)}</ul>}
                {m.answer.after.map((p, j) => <p key={'a' + j}>{p}</p>)}
                {m.answer.link && VIEWS[m.answer.link.view] && (
                  <button className="btn small" onClick={() => nav(VIEWS[m.answer!.link!.view])}>{m.answer.link.label}</button>
                )}
              </>
            ) : <p>{m.text}</p>}
          </div>
        ))}
      </div>
      <div className="chips">
        {chips.map((c) => (
          <button key={c.key} type="button" disabled={waiting} onClick={() => ask({ key: c.key }, c.question).then(() => { if (c.key === 'pickup') toast('Draft added to Messages to approve') })}>{c.question}</button>
        ))}
      </div>
      <form className="send" onSubmit={submit}>
        <input value={q} onChange={(e) => setQ(e.target.value)} type="text" maxLength={aiOn ? 2000 : 300} placeholder={aiOn ? 'Ask about anything, or ask for a change' : 'Ask, or tap a question above'} autoComplete="off" aria-label="Ask the assistant" />
        <button className="btn primary" type="submit" disabled={waiting}>Send</button>
      </form>
    </aside>
  )
}


/** Plain text with "- " lines shown as a list. */
function AiText({ text }: { text: string }) {
  const blocks: { list: boolean; lines: string[] }[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const list = /^[-*•]\s+/.test(line)
    const clean = line.replace(/^[-*•]\s+/, '').replace(/\*\*(.+?)\*\*/g, '$1')
    const last = blocks[blocks.length - 1]
    if (last && last.list === list && list) last.lines.push(clean)
    else blocks.push({ list, lines: [clean] })
  }
  return <>{blocks.map((b, i) => b.list ? <ul key={i}>{b.lines.map((l, j) => <li key={j}>{l}</li>)}</ul> : <p key={i}>{b.lines[0]}</p>)}</>
}
