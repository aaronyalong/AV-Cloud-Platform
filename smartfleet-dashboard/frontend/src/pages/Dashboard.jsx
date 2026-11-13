// P1 Fleet dashboard: critical banner (DT-5), KPI tiles (DT-4), fleet map (DT-3), live alert feed, toasts.
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, qs } from '../api'
import FleetMap from '../components/FleetMap'
import { Kpi, Sev, Status, ago, label } from '../components/ui'
import { usePoll } from '../usePoll'

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const o = ctx.createOscillator(); const g = ctx.createGain()
    o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(ctx.destination)
    o.start(); o.stop(ctx.currentTime + 0.25)
  } catch { /* audio blocked until the user interacts with the page */ }
}

export default function Dashboard({ me }) {
  const nav = useNavigate()
  const [win, setWin] = useState(me.preferences?.window || '24h')
  const [toasts, setToasts] = useState([])
  const cursor = useRef(null)
  const seen = useRef(new Set())

  const summary = usePoll(() => api(`/api/v1/dashboard/summary${qs({ window: win })}`), [win], { report: true })
  const map = usePoll(() => api('/api/v1/dashboard/map'), [], { interval: 2000 })
  const paused = summary.paused

  // D-02 live delta: new or changed alerts since the cursor -> toast for new critical/high alerts (DT-5)
  useEffect(() => { if (summary.data && !cursor.current) cursor.current = summary.data.cursor }, [summary.data])
  usePoll(async () => {
    if (!cursor.current || paused) return null
    const d = await api(`/api/v1/dashboard/live${qs({ since: cursor.current })}`)
    cursor.current = d.cursor
    const fresh = d.alerts.filter((a) => a.status === 'open' && !seen.current.has(a.alert_id) && ['critical', 'high'].includes(a.severity))
    d.alerts.forEach((a) => seen.current.add(a.alert_id))
    if (fresh.length) {
      setToasts((t) => [...fresh.map((a) => ({ id: a.alert_id, a })), ...t].slice(0, 4))
      if (me.preferences?.sound !== false && fresh.some((a) => a.severity === 'critical')) beep()
      fresh.forEach((a) => setTimeout(() => setToasts((t) => t.filter((x) => x.id !== a.alert_id)), 8000))
    }
    return d
  }, [paused], { interval: 2000 })

  const saveWindow = async (w) => {
    setWin(w)
    try { await api('/api/v1/me/preferences', { method: 'PUT', body: { window: w, sound: me.preferences?.sound !== false } }) } catch { /* non-critical */ }
  }

  const s = summary.data
  const feed = s?.recent_alerts || []
  const crit = feed.filter((a) => a.severity === 'critical' && a.status === 'open')
  const oldest = crit.length ? crit[crit.length - 1] : null
  const escalated = oldest && (Date.now() - new Date(oldest.created_at).getTime()) / 1000 > 60
  const open = (a) => nav(`/alerts?id=${a.alert_id}`)

  return (
    <>
      <div className="row">
        <h2 style={{ margin: 0, fontSize: 18 }}>Fleet dashboard</h2>
        <span className="grow" />
        {['1h', '24h', '7d'].map((w) => <button key={w} className={`btn ${w === win ? 'on' : ''}`} onClick={() => saveWindow(w)}>{w}</button>)}
        <button className="btn" onClick={() => { summary.setPaused(!paused); map.setPaused(!paused) }}>{paused ? '▶ Resume' : '⏸ Pause'}</button>
      </div>
      {summary.state === 'stale' && <div className="stale">Data may be out of date: the last refresh failed. Retrying with back-off…</div>}
      {summary.state === 'error' && <div className="err">Cannot reach the API. Retrying every 30 s. <button className="btn" onClick={summary.refresh}>Retry now</button></div>}
      {oldest && (
        <div className={`banner ${escalated ? 'sticky' : ''}`}>
          <b>⚠ CRITICAL</b>
          <span>{label(oldest.type)} · {oldest.vehicle_id} · unacknowledged {ago(oldest.created_at)}{crit.length > 1 ? ` · ${crit.length} critical alerts open` : ''}</span>
          {escalated && <b>· escalated to tenant admin</b>}
          <span className="grow" />
          <button className="btn" onClick={() => open(oldest)}>Open alert</button>
        </div>
      )}
      <div className="kpis">
        {(s?.kpis || []).map((k) => <Kpi key={k.key} k={k} onClick={['open_alerts', 'critical_unacked'].includes(k.key) ? () => nav('/alerts') : undefined} />)}
      </div>
      <div className="grid2">
        <div className="card" style={{ display: 'flex', flexDirection: 'column' }}>
          <h4>Fleet map · CARLA Town03</h4>
          <div style={{ flex: 1 }}><FleetMap vehicles={map.data?.vehicles} /></div>
        </div>
        <div className="card">
          <h4>Live alert feed</h4>
          <table>
            <thead><tr><th>Severity</th><th>Type</th><th>Vehicle</th><th>Age</th><th>Status</th></tr></thead>
            <tbody>
              {feed.slice(0, 12).map((a) => (
                <tr key={a.alert_id} className="click" onClick={() => open(a)} style={a.severity === 'critical' && a.status === 'open' ? { background: '#fff5f5' } : undefined}>
                  <td><Sev s={a.severity} /></td><td>{label(a.type)}</td><td>{a.vehicle_id}</td><td>{ago(a.created_at)}</td><td><Status s={a.status} /></td>
                </tr>
              ))}
              {!feed.length && <tr><td colSpan="5" className="muted">No open alerts.</td></tr>}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 8 }}><span className="grow" /><button className="btn" onClick={() => nav('/alerts')}>All alerts →</button></div>
        </div>
      </div>
      <div className="toasts">
        {toasts.map(({ id, a }) => (
          <div key={id} className={`toast ${a.severity}`} onClick={() => open(a)} style={{ cursor: 'pointer' }}>
            <b>{a.severity.toUpperCase()}</b> · {label(a.type)} · {a.vehicle_id}
            <div style={{ fontSize: 11, opacity: 0.8 }}>model {a.model_name} v{a.model_version} · conf {a.confidence?.toFixed(2)} · click to review</div>
          </div>
        ))}
      </div>
    </>
  )
}
