import { ReactNode } from 'react'
export function Csv({ name }: { name: string }) {
  return <a className="exp" href={`/api/export/${name}.csv`} download>Download CSV</a>
}
export function Head({ title, lead, right }: { title: string; lead?: string; right?: ReactNode }) {
  return (
    <div className="hd2">
      <div><h1>{title}</h1>{lead && <p className="lead">{lead}</p>}</div>
      {right}
    </div>
  )
}
export function Loading({ loading, error }: { loading: boolean; error: boolean }) {
  if (loading) return <p className="sub">Loading</p>
  if (error) return <p className="sub">Could not load this. It will try again.</p>
  return null
}
