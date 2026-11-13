// P9 System health: API latency and errors, database and DocumentDB checks, worker lag, tenant usage vs quota.
import { api } from '../api'
import { usePoll } from '../usePoll'

const ICON = { good: '● OK', warning: '▲ Warning', critical: '■ Failing' }
const COLOR = { good: '#0ca30c', warning: '#b07800', critical: '#d03b3b' }

export default function Health({ me }) {
  const h = usePoll(() => api('/api/v1/system/health'), [], { interval: 10000, report: true })
  const d = h.data
  return (
    <>
      <div className="row"><h2 style={{ margin: 0, fontSize: 18 }}>System health</h2><span className="muted">environment: {d?.env} · refreshes every 10 s</span></div>
      <div className="kpis">
        {(d?.checks || []).map((c) => (
          <div key={c.name} className={`card kpi ${c.status}`}>
            <div className="lab"><span>{c.name}</span><span style={{ color: COLOR[c.status] }}>{ICON[c.status]}</span></div>
            <div className="v">{c.value ?? '—'}{c.value != null && c.unit ? <small> {c.unit}</small> : null}</div>
          </div>
        ))}
      </div>
      <div className="card">
        <h4>API traffic (this task, last 5 minutes)</h4>
        <div className="row">{d && Object.entries(d.api).map(([k, v]) => <span key={k} className="st">{k}: <b>{v ?? '—'}</b></span>)}</div>
      </div>
      <div className="card">
        <h4>{me.role === 'platform_admin' ? 'Tenant usage vs quota (all tenants)' : 'Your tenant usage vs quota'}</h4>
        <table>
          <thead><tr><th>Tenant</th><th>Vehicles</th><th>Online now</th><th>Alerts 24 h</th><th style={{ width: 260 }}>Quota use</th></tr></thead>
          <tbody>
            {(d?.tenants || []).map((t) => (
              <tr key={t.tenant_id}>
                <td>{t.name} <span className="muted">({t.tenant_id})</span></td><td>{t.vehicles}</td><td>{t.online}</td><td>{t.alerts_24h}</td>
                <td><div className="row"><div className="bar grow"><div style={{ width: `${Math.min(100, (100 * t.vehicles) / t.vehicle_quota)}%` }} /></div>{t.vehicles}/{t.vehicle_quota}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
