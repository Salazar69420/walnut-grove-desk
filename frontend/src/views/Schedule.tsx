import { useLive } from '../live'
import { Csv, Head, Loading } from '../components/bits'

type Shift = { id: number; role: string; label: string; proposed: boolean }
type Day = { weekday: number; day: string; closed: boolean; shifts: Shift[]; desk_at_9: number | null; cover: string | null; busiest: boolean }

export function Schedule() {
  const s = useLive<{ items: Day[] }>('/shifts', ['shifts'])
  return (
    <>
      <Head title="Staff schedule" lead="Who is in, and whether the phones are covered when the calls come." right={<Csv name="shifts" />} />
      <section className="card">
        <Loading loading={s.loading && !s.data} error={s.error && !s.data} />
        <div className="tablewrap">
          <table>
            <thead><tr><th>Day</th><th>Shifts</th><th>Phone cover</th></tr></thead>
            <tbody>
              {(s.data?.items || []).map((d) => (
                <tr key={d.weekday}>
                  <td><b>{d.day}</b></td>
                  <td>
                    {d.shifts.length === 0 ? <span className="tag">Closed, voicemail only</span> : d.shifts.map((x) => (
                      <span key={x.id} className={'shift ' + (x.role === 'desk' || x.role === 'open' ? 'desk' : x.role) + (x.proposed ? ' new' : '')}>{x.label}</span>
                    ))}
                  </td>
                  <td>
                    {d.desk_at_9 === null ? <span className="tag">None</span> : <span className={'tag ' + (d.cover || '')}>{d.desk_at_9} on phones at 9 am</span>}
                    {d.busiest && <> <span className="tag high">Busiest hour of the week</span></>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="note">Receptionists in green-blue, nurses in green, doctor in amber. A dashed shift is new, from an approved suggestion. In the real version time off and swaps live here too.</p>
      </section>
    </>
  )
}
