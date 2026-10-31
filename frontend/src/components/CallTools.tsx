import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { useApp } from '../ctx'
import { Modal } from './ui'
import { Icon } from './icons'

const errText = (x: unknown) => x instanceof ApiError && x.message && x.message !== 'error' && x.message !== 'network' ? x.message : ''

export type PlayState = { state: 'waiting' | 'working' | 'ready' | 'failed'; message?: string; since: number }

/** Asks the server to fetch a recording, then checks quickly until it is ready (first check after 0.6 s, then every second, up to 2 minutes). */
export function usePlayer() {
  const { toast } = useApp()
  const [st, setSt] = useState<Record<number, PlayState>>({})
  const [, tick] = useState(0)
  const timers = useRef<Record<number, number>>({})
  const put = (id: number, v: Omit<PlayState, 'since'> | null) => setSt((s) => {
    const n = { ...s }
    if (v) n[id] = { ...v, since: s[id]?.since ?? Date.now() }; else delete n[id]
    return n
  })
  const stop = (id: number) => { window.clearTimeout(timers.current[id]); delete timers.current[id] }
  const close = useCallback((id: number) => { stop(id); put(id, null) }, [])
  const pending = Object.values(st).some((v) => v.state === 'waiting' || v.state === 'working')
  useEffect(() => { if (!pending) return; const t = window.setInterval(() => tick((n) => n + 1), 1000); return () => window.clearInterval(t) }, [pending])
  const play = useCallback(async (id: number) => {
    stop(id)
    setSt((s) => ({ ...s, [id]: { state: 'waiting', since: Date.now() } }))
    try {
      const r = await api<{ state: string }>(`/phone/calls/${id}/listen`, { method: 'POST', quiet: true })
      if (r.state === 'ready') { put(id, { state: 'ready' }); return }
    } catch (x) { put(id, null); toast(errText(x) || 'Could not start the recording.'); return }
    const started = Date.now()
    const poll = async () => {
      try {
        const r = await api<{ state: string; message?: string }>(`/phone/calls/${id}/listen`, { quiet: true })
        if (r.state === 'ready') { put(id, { state: 'ready' }); return }
        if (r.state === 'failed') { put(id, { state: 'failed', message: r.message }); return }
        put(id, { state: r.state === 'working' ? 'working' : 'waiting' })
      } catch { /* try again below */ }
      if (Date.now() - started > 120000) { put(id, { state: 'failed', message: 'The recording is taking too long. Try again in a minute.' }); return }
      timers.current[id] = window.setTimeout(poll, 1000)
    }
    timers.current[id] = window.setTimeout(poll, 600)
  }, [toast])
  useEffect(() => () => { Object.keys(timers.current).forEach((k) => window.clearTimeout(timers.current[Number(k)])) }, [])
  return { st, play, close }
}

export function PlayRow({ id, v, player, cols }: { id: number; v: PlayState; player: ReturnType<typeof usePlayer>; cols: number }) {
  const secs = Math.max(0, Math.round((Date.now() - v.since) / 1000))
  return (
    <tr className="playr"><td colSpan={cols}>
      {v.state === 'ready' ? (
        <div className="aplay"><audio controls autoPlay ref={(el) => { el?.play().catch(() => { /* the browser asked for a tap: the controls are right here */ }) }} src={`/api/phone/calls/${id}/audio`} aria-label="Call recording" /><button className="btn small ghost" onClick={() => player.close(id)}>Close</button></div>
      ) : v.state === 'failed' ? (
        <div className="aplay"><span className="errt" role="alert">{v.message}</span><button className="btn small" onClick={() => player.play(id)}>Try again</button><button className="btn small ghost" onClick={() => player.close(id)}>Close</button></div>
      ) : (
        <div className="aplay" role="status" aria-live="polite"><span className="spin" aria-hidden="true" />
          <span>{v.state === 'working' ? 'Downloading the recording from TELUS' : secs > 25 ? 'Still waiting for the download helper to start' : 'Asking the server for the recording'}<span className="mute"> ({secs} s)</span></span>
          <button className="btn small ghost" onClick={() => player.close(id)}>Cancel</button></div>
      )}
    </td></tr>
  )
}


/** Reads a call's transcript (owners only; the server logs each open) and offers a download and a copy button. */
export function TranscriptModal({ callId, onClose }: { callId: number | null; onClose: () => void }) {
  const { toast } = useApp()
  const [text, setText] = useState<string | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    setText(null); setErr('')
    if (callId) api<{ item: { transcript?: string | null } }>(`/phone/calls/${callId}`, { quiet: true })
      .then((r) => setText(r.item.transcript || 'There is no transcript for this call.')).catch((x) => setErr(errText(x) || 'Could not open the transcript.'))
  }, [callId])
  async function copy() {
    try { await navigator.clipboard.writeText(text || ''); toast('Copied') } catch { toast('Could not copy. Select the text and copy it.') }
  }
  return (
    <Modal open={!!callId} onClose={onClose} title="Call transcript" footer={<>
      <button className="btn" onClick={copy} disabled={!text}>Copy</button>
      <a className="btn" href={`/api/phone/calls/${callId}/transcript.txt`} download><Icon n="download" size={16} /> Download</a>
      <button className="btn primary" onClick={onClose}>Close</button></>}>
      {err ? <p className="errt" role="alert">{err}</p> : text === null ? <p className="mute">Opening</p> : <pre className="transcript">{text}</pre>}
      <p className="note">Made by automatic transcription, so it can contain mistakes. Opening and downloading are written to the access log.</p>
    </Modal>
  )
}

export type CallMeta = { id: number; has_recording: boolean; has_transcript: boolean; seconds: number }

/** Listen, read and download buttons for one call. Shown only to people allowed to use them. */
export function CallButtons({ call, player, onTranscript }: { call: CallMeta; player: ReturnType<typeof usePlayer>; onTranscript: (id: number) => void }) {
  const { can } = useApp()
  const v = player.st[call.id]
  const loading = v && (v.state === 'waiting' || v.state === 'working')
  const listen = can('phone.listen') && call.has_recording
  const read = can('phone.transcript') && call.has_transcript
  if (!listen && !read) return null
  return (
    <span className="cbtns">
      {listen && <button className={'playb sm' + (v ? ' on' : '')} aria-busy={loading || undefined} aria-label="Listen to this call" title="Listen to this call" onClick={() => (v ? player.close(call.id) : player.play(call.id))}>
        {loading ? <span className="spin" aria-hidden="true" /> : <Icon n={v?.state === 'ready' ? 'pause' : 'play'} size={15} />}</button>}
      {read && <button className="playb sm" aria-label="Read the transcript" title="Read the transcript" onClick={() => onTranscript(call.id)}><Icon n="note" size={15} /></button>}
      {read && <a className="playb sm" aria-label="Download the transcript" title="Download the transcript (.txt)" href={`/api/phone/calls/${call.id}/transcript.txt`} download><Icon n="download" size={15} /></a>}
    </span>
  )
}
