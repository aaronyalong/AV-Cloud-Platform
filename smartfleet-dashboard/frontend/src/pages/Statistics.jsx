// P8 Statistics: alerts by type over time, hour x weekday heatmap, latency vs SLO, false-alarm rate, CSV export.
import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, qs } from '../api'
import { SEV_COLORS, TYPE_COLORS, label } from '../components/ui'
import { usePoll } from '../usePoll'

const RANGES = { '24h': 1, '7d': 7, '30d': 30 }
const BLUES = ['#f2f7fd', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95']

function tick(ts, bucket) {
  const d = new Date(ts)
  return bucket === 'hour' || bucket === 'minute' ? d.toLocaleString([], { weekday: 'short', hour: '2-digit' }) : d.toLocaleDateString()
}

function Heatmap({ data }) {
  if (!data) return null
  const cell = (v) => BLUES[Math.min(BLUES.length - 1, Math.floor((v / (data.max || 1)) * (BLUES.length - 1)))]
  return (
    <svg viewBox="0 0 520 190" style={{ width: '100%' }}>
      {data.days.map((d, r) => (
        <g key={d}>
          <text x="0" y={r * 22 + 15} fontSize="10" fill="#52606d">{d}</text>
          {data.grid[r].map((v, h) => (
            <rect key={h} x={34 + h * 20} y={r * 22} width="18" height="20" rx="2" fill={cell(v)}><title>{`${d} ${h}:00 — ${v} alerts`}</title></rect>
          ))}
        </g>
      ))}
      {[0, 4, 8, 12, 16, 20].map((h) => <text key={h} x={34 + h * 20} y="172" fontSize="10" fill="#52606d">{h}:00</text>)}
      <text x="34" y="187" fontSize="9" fill="#8a96a3">Light → dark = fewer → more alerts (local time). Hover a cell for the count.</text>
    </svg>
  )
}

export default function Statistics() {
  const [range, setRange] = useState('7d')
  const [groupBy, setGroupBy] = useState('type')
  const [table, setTable] = useState(false)
  const [exp, setExp] = useState(null)
  const from = new Date(Date.now() - RANGES[range] * 86400e3).toISOString()
  const p = qs({ from })
  const series = usePoll(() => api(`/api/v1/stats/alerts${qs({ from, group_by: groupBy })}`), [range, groupBy], { interval: 30000, report: true })
  const heat = usePoll(() => api(`/api/v1/stats/heatmap${p}`), [range], { interval: 60000 })
  const lat = usePoll(() => api(`/api/v1/stats/latency${p}`), [range], { interval: 30000 })
  const dq = usePoll(() => api(`/api/v1/stats/detection-quality${p}`), [range], { interval: 60000 })

  const s = series.data
  const colors = groupBy === 'type' ? TYPE_COLORS : SEV_COLORS
  const o = lat.data?.overall || {}
  const far = [...(dq.data?.by_type || [])].sort((a, b) => b.far - a.far).map((r) => ({ ...r, pct: Math.round(r.far * 1000) / 10, name: label(r.type) }))

  const doExport = async () => {
    setExp({ status: 'queued' })
    try {
      const j = await api('/api/v1/stats/exports', { method: 'POST', body: { days: RANGES[range] } })
      for (let i = 0; i < 20; i += 1) {
        await new Promise((r) => setTimeout(r, 1000))
        const st = await api(`/api/v1/stats/exports/${j.export_id}`)
        if (st.status === 'done') { setExp(st); return }
      }
      setExp({ status: 'timeout' })
    } catch (e) { setExp({ status: `error: ${e.message}` }) }
  }

  return (
    <>
      <div className="row">
        <h2 style={{ margin: 0, fontSize: 18 }}>Statistics</h2><span className="grow" />
        {Object.keys(RANGES).map((r) => <button key={r} className={`btn ${r === range ? 'on' : ''}`} onClick={() => setRange(r)}>{r}</button>)}
        <select value={groupBy} onChange={(e) => setGroupBy(e.target.value)}><option value="type">Group by type</option><option value="severity">Group by severity</option></select>
        <button className="btn" onClick={() => setTable(!table)}>{table ? 'Chart view' : 'Table view'}</button>
        <button className="btn p" onClick={doExport}>⇩ Export CSV</button>
      </div>
      {exp && <div className="stale">Export: {exp.status}{exp.url && <> · {exp.rows} rows · <a href={exp.url}>download CSV</a> (link valid 24 h)</>}</div>}
      <div className="kpis">
        {[['Alerts in range', s?.total], ['Latency p50', o.p50, 's'], ['Latency p95', o.p95, 's'], ['MTTA', o.mtta, 's'], ['MTTR', o.mttr ? Math.round(o.mttr / 60) : null, 'min'], ['SLO', 3.0, 's']].map(([l, v, u]) => (
          <div key={l} className="card kpi"><div className="lab">{l}</div><div className="v">{v ?? '—'}{v != null && u ? <small> {u}</small> : null}</div></div>
        ))}
      </div>
      <div className="grid2e">
        <div className="card">
          <h4>Alerts per {s?.bucket || 'bucket'} by {groupBy}</h4>
          {table ? (
            <div style={{ maxHeight: 260, overflow: 'auto' }}>
              <table><thead><tr><th>Time</th>{s?.groups.map((g) => <th key={g}>{label(g)}</th>)}</tr></thead>
                <tbody>{s?.points.map((pt) => <tr key={pt.ts}><td>{tick(pt.ts, s.bucket)}</td>{s.groups.map((g) => <td key={g}>{pt[g]}</td>)}</tr>)}</tbody></table>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={s?.points || []} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="#ecebe6" />
                <XAxis dataKey="ts" tickFormatter={(t) => tick(t, s?.bucket)} minTickGap={40} fontSize={10} />
                <YAxis allowDecimals={false} fontSize={10} />
                <Tooltip labelFormatter={(t) => new Date(t).toLocaleString()} formatter={(v, n) => [v, label(n)]} />
                <Legend formatter={label} wrapperStyle={{ fontSize: 11 }} />
                {(s?.groups || []).map((g) => <Bar key={g} dataKey={g} stackId="a" fill={colors[g] || '#8a96a3'} stroke="#fff" strokeWidth={0.5} />)}
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="card"><h4>Alert density · hour × weekday</h4><Heatmap data={heat.data} /></div>
      </div>
      <div className="grid2e">
        <div className="card">
          <h4>Event-to-display latency (p50 / p95) vs 3 s SLO</h4>
          <ResponsiveContainer width="100%" height={230}>
            <LineChart data={lat.data?.series || []} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="#ecebe6" />
              <XAxis dataKey="ts" tickFormatter={(t) => tick(t, lat.data?.bucket)} minTickGap={40} fontSize={10} />
              <YAxis domain={[0, (max) => Math.max(4, Math.ceil(max))]} unit=" s" fontSize={10} />
              <Tooltip labelFormatter={(t) => new Date(t).toLocaleString()} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <ReferenceLine y={3} stroke="#d03b3b" strokeDasharray="4 3" label={{ value: 'SLO 3.0 s', position: 'insideTopRight', fontSize: 10, fill: '#1b2430' }} />
              <Line type="monotone" dataKey="p95" stroke="#2a78d6" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="p50" stroke="#eb6834" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <h4>False-alarm rate by alert type (closed alerts)</h4>
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={far} layout="vertical" margin={{ top: 4, right: 30, left: 30, bottom: 0 }}>
              <CartesianGrid horizontal={false} stroke="#ecebe6" />
              <XAxis type="number" unit="%" fontSize={10} />
              <YAxis type="category" dataKey="name" width={100} fontSize={10} />
              <Tooltip formatter={(v, n, it) => [`${v}% (${it.payload.false_alarms} of ${it.payload.closed})`, 'False-alarm rate']} />
              <Bar dataKey="pct" fill="#2a78d6" radius={[0, 4, 4, 0]} label={{ position: 'right', fontSize: 10, formatter: (v) => `${v}%` }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="card">
        <h4>Detections by model version</h4>
        <table>
          <thead><tr><th>Model</th><th>Version</th><th>Alerts</th><th>False alarms</th><th>Avg confidence</th></tr></thead>
          <tbody>{(dq.data?.by_model || []).map((m) => <tr key={m.model_name + m.model_version}><td>{m.model_name}</td><td>v{m.model_version}</td><td>{m.alerts}</td><td>{m.false_alarms}</td><td>{m.avg_conf}</td></tr>)}</tbody>
        </table>
      </div>
    </>
  )
}
