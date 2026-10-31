import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'

type View = {
  enabled: boolean; staff_enabled: boolean; model: string; has_key: boolean; key_last4: string | null; deny_data_collection: boolean
  system_prompt: string; default_prompt: string; agent_name?: string; rules: string; active_for: string[]; knowledge_count: number; fee_count: number
}
type Model = { id: string; name: string; context: number | null; in_per_million: number; out_per_million: number }
type Tried = { kind: string; reply: string | null; model: string | null; code?: string; sources?: { title: string }[]; reason?: string; usage?: { cost?: number; prompt_tokens?: number; completion_tokens?: number } }
const err = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' ? x.message : 'Not saved.'

export function AssistantTab() {
  const { toast } = useApp()
  const [v, setV] = useState<View | null>(null)
  const [models, setModels] = useState<Model[]>([])
  const [q, setQ] = useState('deepseek')
  const [key, setKey] = useState('')
  const [prompt, setPrompt] = useState('')
  const [full, setFull] = useState<string | null>(null)
  const [tryText, setTryText] = useState('Are you open on Sunday?')
  const [tried, setTried] = useState<Tried | null>(null)
  const [busy, setBusy] = useState(false)
  const load = () => api<View>('/admin/assistant', { quiet: true }).then((r) => { setV(r); setPrompt(r.system_prompt) }).catch(() => {})
  useEffect(() => { load(); api<{ items: Model[] }>('/admin/assistant/models', { quiet: true }).then((r) => setModels(r.items)).catch(() => {}) }, [])
  const shown = useMemo(() => models.filter((m) => !q.trim() || (m.id + ' ' + m.name).toLowerCase().includes(q.trim().toLowerCase())).slice(0, 60), [models, q])
  async function put(body: Record<string, unknown>, msg: string) {
    setBusy(true)
    try { const r = await api<View>('/admin/assistant', { method: 'PUT', body, quiet: true }); setV(r); setPrompt(r.system_prompt); toast(msg); setFull(null) } catch (x) { toast(err(x)) }
    setBusy(false)
  }
  async function tryIt() {
    setBusy(true); setTried(null)
    try { setTried(await api<Tried>('/admin/assistant/try', { method: 'POST', body: { text: tryText }, quiet: true })) } catch (x) { toast(err(x)) }
    setBusy(false)
  }
  if (!v) return <section className="card"><h2>AI assistant</h2><p className="sub">Loading</p></section>
  const current = models.find((m) => m.id === v.model)
  return (
    <>
      <section className="card">
        <h2>AI assistant</h2>
        <p className="note">When this is on, the assistant's replies are written by an AI model through OpenRouter, using only knowledge you have approved. Emergencies and medical questions are always answered with fixed text, never by the AI. Every AI reply is checked: it must cite approved knowledge, and every price or time in it must come from that knowledge, or the safe reply is used instead.</p>
        <div className="setrow"><b>AI writing</b>
          <label className="ck"><input type="checkbox" checked={v.enabled} disabled={busy} onChange={(e) => put({ enabled: e.target.checked }, e.target.checked ? 'AI assistant on' : 'AI assistant off')} /> {v.enabled ? 'On' : 'Off (the rules answer)'}</label></div>
        <div className="setrow"><b>AI for staff</b>
          <label className="ck"><input type="checkbox" checked={v.staff_enabled} disabled={busy} onChange={(e) => put({ staff_enabled: e.target.checked }, e.target.checked ? 'AI for staff on' : 'AI for staff off')} /> The Assistant panel answers any question about the app's data and can propose changes (staff confirm each one)</label></div>
        {v.staff_enabled && <p className="note">This sends what staff ask about (names, messages, tasks, appointments) to OpenRouter. Call transcripts, recordings and keys are never sent. Each person only reaches what their role allows.</p>}
        <div className="setrow"><b>Active for</b><span>{v.active_for.length ? v.active_for.join(', ') : 'Nobody yet'} <span className="mute small">(people on the texting test list with their assistant switch on; nobody else's messages are sent to the AI)</span></span></div>
        <div className="setrow"><b>Knowledge it uses</b><span>{v.knowledge_count} approved entries and {v.fee_count} approved fees</span></div>
      </section>

      <section className="card">
        <h2>OpenRouter</h2>
        <div className="setrow"><b>API key</b><span>{v.has_key ? <>Saved, ends in <code>{v.key_last4}</code></> : 'Not set'}</span></div>
        <div className="setrow"><b>{v.has_key ? 'Replace key' : 'Add key'}</b>
          <span className="rowbtn"><input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-or-v1-..." autoComplete="off" aria-label="OpenRouter API key" />
            <button className="btn" disabled={busy || key.trim().length < 10} onClick={() => put({ api_key: key.trim() }, 'Key saved').then(() => setKey(''))}>Save key</button>
            {v.has_key && <button className="btn ghost" disabled={busy} onClick={() => put({ clear_key: true, enabled: false }, 'Key removed')}>Remove</button>}</span></div>
        <p className="note">The key is stored encrypted and never shown again.</p>
        <div className="setrow"><b>Model</b><span><code>{v.model}</code>{current && <span className="mute small"> {current.name}: ${current.in_per_million} in, ${current.out_per_million} out per million tokens</span>}</span></div>
        <div className="setrow"><b>Choose a model</b>
          <span className="modelpick"><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search models" aria-label="Search models" />
            <select size={8} value={v.model} onChange={(e) => put({ model: e.target.value }, 'Model saved')} aria-label="Model">
              {shown.map((m) => <option key={m.id} value={m.id}>{m.id}  (${m.in_per_million} / ${m.out_per_million} per M)</option>)}
            </select></span></div>
        <p className="note">Only models that can return checked JSON are listed. Prices are per million tokens; one text usually costs a small fraction of a cent.</p>
        <AgentName value={v.agent_name || 'Hazel'} busy={busy} onSave={(n) => put({ agent_name: n }, `The assistant is now called ${n}`)} />
        <div className="setrow"><b>Privacy</b>
          <label className="ck"><input type="checkbox" checked={v.deny_data_collection} disabled={busy} onChange={(e) => put({ deny_data_collection: e.target.checked }, 'Saved')} /> Only use providers that do not store or train on the messages</label></div>
      </section>

      <section className="card">
        <h2>Base prompt</h2>
        <p className="note">How the assistant should sound and behave. It improves on its own as you approve knowledge and correct replies, because every approved entry is added to what it reads. The hard rules below are always added and cannot be edited here.</p>
        <textarea className="promptbox" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={14} maxLength={6000} aria-label="Base prompt" />
        <div className="rowbtn"><button className="btn primary" disabled={busy || prompt === v.system_prompt} onClick={() => put({ system_prompt: prompt }, 'Prompt saved')}>Save prompt</button>
          <button className="btn ghost" disabled={busy} onClick={() => setPrompt(v.default_prompt)}>Reset to the default</button>
          <button className="btn ghost" onClick={() => api<{ system_prompt: string }>('/admin/assistant/prompt', { quiet: true }).then((r) => setFull(r.system_prompt)).catch(() => {})}>Show everything the AI reads</button></div>
        <details><summary>Hard rules (always added)</summary><pre className="transcript">{v.rules}</pre></details>
        {full && <pre className="transcript">{full}</pre>}
      </section>

      <section className="card">
        <h2>Try it</h2>
        <p className="note">See what the assistant would answer. Nothing is sent and no task is raised.</p>
        <div className="rowbtn"><input value={tryText} onChange={(e) => setTryText(e.target.value)} maxLength={800} aria-label="A message from a client" style={{ flex: 1 }} />
          <button className="btn primary" disabled={busy || tryText.trim().length < 2} onClick={tryIt}>{busy ? 'Asking' : 'Try'}</button></div>
        {tried && (
          <div className="pvw">
            <p className="small mute">{tried.kind === 'fallback' ? `The AI reply did not pass the checks (${tried.code}), so the rules would answer.` : tried.model ? `Written by ${tried.model}` : 'Fixed text'}{tried.usage?.cost ? `, cost $${tried.usage.cost.toFixed(5)}` : ''}</p>
            {tried.reply && <p className="quote">{tried.reply}</p>}
            {tried.sources && tried.sources.length > 0 && <p className="small mute">From: {tried.sources.map((s) => s.title).join(', ')}</p>}
            {tried.reason && <p className="small mute">AI note: {tried.reason}</p>}
          </div>
        )}
      </section>
    </>
  )
}

function AgentName({ value, busy, onSave }: { value: string; busy: boolean; onSave: (n: string) => void }) {
  const [n, setN] = useState(value)
  return (
    <div className="rowbtn" style={{ alignItems: 'end' }}>
      <label className="form" style={{ flex: 1 }}>Assistant's name (used in texts; it always says it is automated if asked)<input value={n} maxLength={30} onChange={(e) => setN(e.target.value)} /></label>
      <button className="btn" disabled={busy || n.trim() === value || n.trim().length < 2} onClick={() => onSave(n.trim())}>Save name</button>
    </div>
  )
}
