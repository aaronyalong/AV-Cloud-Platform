// Fleet map (UI-1.2): SVG of the Town03-like road grid used by the simulator; markers by connectivity (DT-3).
import { useState } from 'react'
import { SEV_COLORS, ago } from './ui'

const ROADS_X = [-100, 0, 100, 200]
const ROADS_Y = [-150, -50, 50, 150]
const VB = { x: -130, y: -180, w: 360, h: 360 }

export default function FleetMap({ vehicles = [] }) {
  const [sel, setSel] = useState(null)
  const v = vehicles.find((x) => x.vehicle_id === sel)
  return (
    <div style={{ position: 'relative', height: '100%', minHeight: 330 }}>
      <svg viewBox={`${VB.x} ${VB.y} ${VB.w} ${VB.h}`} style={{ width: '100%', height: '100%', background: '#eef2ee', borderRadius: 6 }}>
        <g stroke="#fff" strokeWidth="9">
          {ROADS_X.map((x) => <line key={`x${x}`} x1={x} y1={-170} x2={x} y2={170} />)}
          {ROADS_Y.map((y) => <line key={`y${y}`} x1={-120} y1={y} x2={220} y2={y} />)}
        </g>
        <g stroke="#d9dfd9" strokeWidth="0.6" strokeDasharray="3 3">
          {ROADS_X.map((x) => <line key={`dx${x}`} x1={x} y1={-170} x2={x} y2={170} />)}
          {ROADS_Y.map((y) => <line key={`dy${y}`} x1={-120} y1={y} x2={220} y2={y} />)}
        </g>
        {vehicles.filter((c) => c.x != null).map((c) => {
          const alert = c.open_severity
          const fill = c.status === 'online' ? '#2a78d6' : '#9aa7b4'
          return (
            <g key={c.vehicle_id} transform={`translate(${c.x} ${c.y})`} onClick={() => setSel(c.vehicle_id)} style={{ cursor: 'pointer' }}>
              {alert && <circle r="11" fill="none" stroke={SEV_COLORS[alert]} strokeWidth="2.5"><animate attributeName="r" values="8;13;8" dur="1.6s" repeatCount="indefinite" /></circle>}
              <circle r="5.5" fill={c.status === 'offline' ? '#fff' : (alert ? SEV_COLORS[alert] : fill)} stroke={c.status === 'offline' ? '#9aa7b4' : '#fff'} strokeWidth="2" />
              {(alert || c.status !== 'online') && (
                <text x="9" y="-8" fontSize="9" fill={alert ? '#8a1f1f' : '#52606d'} fontWeight={alert ? 700 : 400}>
                  {c.vehicle_id}{c.status !== 'online' ? ` · ${c.status}` : ''}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <div className="legend" style={{ position: 'absolute', left: 8, bottom: 8, background: '#fff', border: '1px solid var(--line)', borderRadius: 5, padding: '4px 8px' }}>
        <span><i style={{ background: '#2a78d6', borderRadius: 5 }} />Online</span>
        <span><i style={{ background: '#9aa7b4', borderRadius: 5 }} />Stale</span>
        <span><i style={{ background: '#fff', border: '1.5px solid #9aa7b4', borderRadius: 5 }} />Offline</span>
        <span><i style={{ background: SEV_COLORS.critical, borderRadius: 5 }} />Open alert</span>
      </div>
      {v && (
        <div className="card" style={{ position: 'absolute', right: 8, top: 8, width: 210 }}>
          <div className="row"><b>{v.name}</b><span className="grow" /><button className="btn" onClick={() => setSel(null)}>×</button></div>
          <div className="kv" style={{ marginTop: 6 }}>
            <div>Status</div><div>{v.status}</div>
            <div>Speed</div><div>{v.speed_kmh != null ? `${v.speed_kmh} km/h` : '—'}</div>
            <div>Run</div><div>{v.run_id}</div>
            <div>Heartbeat</div><div>{ago(v.last_heartbeat)} ago</div>
            <div>Position</div><div>{v.x?.toFixed(0)}, {v.y?.toFixed(0)}</div>
          </div>
        </div>
      )}
    </div>
  )
}
