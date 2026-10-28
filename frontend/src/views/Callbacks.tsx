import { useState } from 'react'
import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Csv, Head, Loading } from '../components/bits'

type Cb = { id: number; client_label: string; pet_label: string; what: string; kind: string; via: string; at_display: string; priority: string; red_flag: boolean; owner_id: number | null; owner_name: string; status: string }
type Draft = { id: number; kind: string; to_label: string; body: string; status: string }

export function Callbacks() {
  const { can, toast, run, busy } = useApp()
  const [filter, setFilter] = useState('all')
  const cbs = useLive<{ items: Cb[] }>('/callbacks?filter=' + filter, ['callbacks'])
  const showDrafts = can('drafts.view')
  const drafts = useLive<{ items: Draft[] }>(showDrafts ? '/drafts' : null, ['drafts'], showDrafts)
  const [edit, setEdit] = useState<Record<number, string>>({})

  const act = (key: string, path: string, msg: string, body?: unknown) =>
    run(key, async () => { await api(path, { method: 'POST', body }); toast(msg); cbs.reload(); drafts.reload() })

  const items = cbs.data?.items || []
  const pend = (drafts.data?.items || []).filter((d) => d.status === 'pending')
  return (
    <>
      <Head title="Callbacks" lead="Everything clients asked for by voicemail, missed call or text. Shared by everyone at the desk, so no one calls the same person twice." right={<Csv name="callbacks" />} />
      <section className="card">
        <div className="filters" role="group" aria-label="Filter">
          {([['all', 'All'], ['mine', 'Mine'], ['high', 'High priority']] as const).map(([k, l]) => (
            <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
        <Loading loading={cbs.loading && !cbs.data} error={cbs.error && !cbs.data} />
        {cbs.data && items.length === 0 && <p className="sub">Nothing here.</p>}
        {items.map((c) => (
          <div key={c.id} className={'row' + (c.status === 'done' ? ' done' : '')} data-cb={c.id}>
            <div>
              <div className="who">{c.client_label}{c.pet_label && <span style={{ fontWeight: 400, color: 'var(--mute)' }}> for {c.pet_label}</span>}</div>
              <div className="what">{c.what}</div>
              <div className="meta">
                <span className="tag">{c.kind}</span>
                <span className="tag">{c.via}, {c.at_display}</span>
                {c.priority === 'high' && <span className="tag high">High priority</span>}
                {c.red_flag && <span className="tag high">Possible red flag</span>}
                {c.owner_name && c.status !== 'done' && <span className="tag acc">With {c.owner_name}</span>}
              </div>
            </div>
            <div>
              {c.status === 'done' ? <span className="status-done">Done</span>
                : c.status === 'new' ? <button className="btn primary small" disabled={busy.has('cl' + c.id)} onClick={() => act('cl' + c.id, `/callbacks/${c.id}/claim`, 'Assigned to you')}>I will call back</button>
                : <button className="btn small" disabled={busy.has('cd' + c.id)} onClick={() => act('cd' + c.id, `/callbacks/${c.id}/done`, 'Marked done')}>Mark done</button>}
            </div>
          </div>
        ))}
      </section>
      {showDrafts && (
        <section className="card">
          <h2>Messages to approve</h2>
          <p className="sub">Drafted by the assistant from your own clinic wording. Nothing is sent until you approve it. {pend.length ? `${pend.length} waiting.` : 'All caught up.'}</p>
          {(drafts.data?.items || []).map((d) => d.status === 'sent' ? (
            <div className="draft" key={d.id}><div className="to"><span className="tag ok">Sent (mock)</span> {d.kind} to {d.to_label}</div></div>
          ) : (
            <div className="draft" key={d.id} data-draft={d.id}>
              <div className="to"><span className="tag acc">{d.kind}</span> To {d.to_label}, by text</div>
              {edit[d.id] !== undefined ? (
                <>
                  <textarea value={edit[d.id]} maxLength={2000} aria-label="Message text" onChange={(e) => setEdit({ ...edit, [d.id]: e.target.value })} />
                  <button className="btn primary small" disabled={busy.has('sv' + d.id) || !edit[d.id].trim()} onClick={() => run('sv' + d.id, async () => {
                    await api(`/drafts/${d.id}`, { method: 'PATCH', body: { body: edit[d.id] } })
                    const e = { ...edit }; delete e[d.id]; setEdit(e); toast('Saved'); drafts.reload()
                  })}>Save</button>
                </>
              ) : (
                <>
                  <p>{d.body}</p>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <button className="btn primary small" disabled={busy.has('ap' + d.id)} onClick={() => act('ap' + d.id, `/drafts/${d.id}/approve`, 'Mock only: a real version would send this text now')}>Approve and send</button>
                    <button className="btn small" onClick={() => setEdit({ ...edit, [d.id]: d.body })}>Edit</button>
                  </div>
                </>
              )}
            </div>
          ))}
        </section>
      )}
    </>
  )
}
