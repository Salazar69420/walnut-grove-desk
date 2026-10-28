import { api } from '../api/client'
import { useApp } from '../ctx'
import { useLive } from '../live'
import { Csv, Head, Loading } from '../components/bits'

type R = { id: number; pet_label: string; owner_label: string; test: string; age_days: number; stage: number; stage_names: string[]; late: boolean }

export function Results() {
  const { can, toast, run, busy } = useApp()
  const rs = useLive<{ items: R[] }>('/results', ['results'])
  const items = rs.data?.items || []
  return (
    <>
      <Head title="Lab results" lead="From the lab to the client, one step at a time. Anything waiting two days or more turns amber and shows up in Dr. Garg's brief." right={<Csv name="results" />} />
      <section className="card">
        <Loading loading={rs.loading && !rs.data} error={rs.error && !rs.data} />
        {items.map((r) => (
          <div key={r.id} className={'row' + (r.stage === 2 ? ' done' : '')} data-result={r.id}>
            <div>
              <div className="who">{r.pet_label} <span style={{ fontWeight: 400, color: 'var(--mute)' }}>{r.owner_label}</span></div>
              <div className="what">{r.test}</div>
              <div className="meta">
                <div className="stages">{r.stage_names.map((n, i) => <span key={n} className={i <= r.stage ? 'on' : ''}>{n}</span>)}</div>
                {r.stage < 2 && <span className={'tag' + (r.late ? ' warn' : '')}>{r.age_days === 0 ? 'Arrived today' : `Waiting ${r.age_days} ${r.age_days === 1 ? 'day' : 'days'}`}</span>}
              </div>
            </div>
            <div>
              {r.stage === 2 ? <span className="status-done">Closed</span>
                : can('results.advance') ? (
                  <button className="btn primary small" disabled={busy.has('ad' + r.id)} onClick={() => run('ad' + r.id, async () => {
                    await api(`/results/${r.id}/advance`, { method: 'POST', body: { from_stage: r.stage } })
                    toast(r.stage === 0 ? 'Marked reviewed' : 'Marked client told'); rs.reload()
                  })}>{r.stage === 0 ? 'Mark reviewed' : 'Client told'}</button>
                ) : null}
            </div>
          </div>
        ))}
      </section>
    </>
  )
}
