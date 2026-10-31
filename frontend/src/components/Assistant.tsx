import { FormEvent, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useApp } from '../ctx'

type Answer = { paragraphs: string[]; bullets: string[]; after: string[]; link: { label: string; view: string } | null }
type Msg = { who: 'me' | 'bot'; text?: string; answer?: Answer; typing?: boolean }

const VIEWS: Record<string, string> = { home: '/home', tasks: '/tasks', contacts: '/contacts', brief: '/brief', overnight: '/overnight', callbacks: '/callbacks', results: '/results', schedule: '/schedule', setup: '/setup', tools: '/tools' }

export function Assistant() {
  const { toast } = useApp()
  const nav = useNavigate()
  const [greeting, setGreeting] = useState('')
  const [chips, setChips] = useState<{ key: string; question: string }[]>([])
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [q, setQ] = useState('')
  const [waiting, setWaiting] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api<{ greeting: string; chips: { key: string; question: string }[] }>('/assistant/chips', { quiet: true })
      .then((r) => { setGreeting(r.greeting); setChips(r.chips || []) })
      .catch(() => {})
  }, [])
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
  function submit(e: FormEvent) {
    e.preventDefault()
    const t = q.trim()
    if (!t) return
    setQ('')
    ask({ text: t }, t)
  }
  return (
    <aside className="assist" aria-label="Assistant">
      <header>
        <b><span className="dot" style={{ margin: 0 }} />Assistant</b>
        <span>Reports to Dr. Garg. Answers come from the shared clinic record.</span>
      </header>
      <div className="msgs" id="msgs" aria-live="polite" ref={box}>
        {greeting && <div className="msg"><p>{greeting}</p></div>}
        {msgs.map((m, i) => (
          <div key={i} className={'msg' + (m.who === 'me' ? ' me' : '')}>
            {m.typing ? <span className="typing" aria-label="Typing"><i /><i /><i /></span> : m.answer ? (
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
        <input value={q} onChange={(e) => setQ(e.target.value)} type="text" maxLength={300} placeholder="Ask, or tap a question above" autoComplete="off" aria-label="Ask the assistant" />
        <button className="btn primary" type="submit" disabled={waiting}>Send</button>
      </form>
    </aside>
  )
}
