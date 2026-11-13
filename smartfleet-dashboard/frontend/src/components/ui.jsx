// Small shared UI pieces: severity/status chips, KPI tile, time helpers, chart palette.
export const TYPE_COLORS = { // fixed categorical order, color follows the alert type (never its rank)
  collision: '#2a78d6', pedestrian: '#eb6834', emergency_siren: '#1baf7a',
  stopped_vehicle: '#eda100', red_light: '#e87ba4', horn: '#008300',
}
export const SEV_COLORS = { critical: '#d03b3b', high: '#ec835a', medium: '#fab219', low: '#6b7a8c' }
export const label = (s) => (s || '').replace(/_/g, ' ')

export const Sev = ({ s }) => <span className={`chip ${s}`}>{s}</span>
export const Status = ({ s }) => <span className={`st ${s}`}>{label(s)}</span>

export function ago(ts) {
  if (!ts) return '—'
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000)
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.round(s / 60)} m`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  return `${Math.round(s / 86400)} d`
}
export const fmtTime = (ts) => (ts ? new Date(ts).toLocaleString() : '—')

const STATUS_TEXT = { good: '● Good', warning: '▲ Warning', critical: '■ Critical', neutral: '' }
export function Kpi({ k, onClick }) {
  return (
    <div className={`card kpi ${k.status}`} onClick={onClick} style={{ cursor: onClick ? 'pointer' : 'default' }}>
      <div className="lab"><span>{k.label}</span><span>{STATUS_TEXT[k.status]}</span></div>
      <div className="v">
        {k.value ?? '—'}{k.unit && k.value != null ? <small> {k.unit}</small> : null}
        {k.total != null ? <small> / {k.total}</small> : null}
      </div>
      <div className="d">{k.sub}</div>
    </div>
  )
}

export const LIVE_COLORS = { live: '#5ee05e', stale: '#fab219', error: '#ff6b6b', paused: '#c8ced6', loading: '#c8ced6' }
