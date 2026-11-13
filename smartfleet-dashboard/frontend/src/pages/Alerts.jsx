// P3 Alerts workspace: filter bar, paged table, detail drawer with evidence, action panel (DT-2), history.
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, newIdemKey, qs } from '../api'
import { Sev, Status, ago, fmtTime, label } from '../components/ui'
import { usePoll } from '../usePoll'

const STATUSES = ['open', 'acknowledged', 'assigned', 'dispatched', 'resolved', 'false_alarm']
const SEVERITIES = ['critical', 'high', 'medium', 'low']
const TYPES = ['collision', 'pedestrian', 'emergency_siren', 'stopped_vehicle', 'red_light', 'horn']
// DT-2 (mirrors the server rules so the UI only offers valid actions; the server re-checks every request)
const DT2 = {
  acknowledge: { from: ['open'], text: 'Acknowledge' },
  assign: { from: ['acknowledged'], text: 'Assign' },
  dispatch: { from: ['acknowledged', 'assigned'], text: 'Record dispatch', note: true },
  resolve: { from: ['acknowledged', 'assigned', 'dispatched'], text: 'Resolve', note: true },
  false_alarm: { from: ['open', 'acknowledged', 'assigned'], text: 'Mark false alarm', note: true },
}

function Toggle({ values, sel, setSel }) {
  return values.map((v) => (
    <button key={v} className={`btn ${sel.includes(v) ? 'on' : ''}`} onClick={() => setSel(sel.includes(v) ? sel.filter((x) => x !== v) : [...sel, v])}>{label(v)}</button>
  ))
}

function Drawer({ id, me, onClose, onChanged }) {
  const [a, setA] = useState(null)
  const [ev, setEv] = useState({})
  const [users, setUsers] = useState([])
  const [note, setNote] = useState('')
  const [assignee, setAssignee] = useState('')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const canAct = me.permissions.includes('alerts:act')

  const load = useCallback(async () => {
    const d = await api(`/api/v1/alerts/${id}`)
    setA(d)
    return d
  }, [id])

  useEffect(() => {
    setA(null); setEv({}); setMsg(null); setNote('')
    load().then(async (d) => {
      const out = {}
      for (const kind of ['frame', 'audio']) {
        if (d.evidence?.[kind]) {
          try { out[kind] = (await api(`/api/v1/alerts/${id}/evidence${qs({ kind })}`)).url } catch { /* missing evidence */ }
        }
      }
      setEv(out)
    }).catch((e) => setMsg({ err: true, text: e.message }))
    if (canAct) api('/api/v1/users').then(setUsers).catch(() => {})
  }, [id, load, canAct])

  const act = async (action) => {
    setBusy(true); setMsg(null)
    try {
      const fresh = await load()                                        // BR-5: refetch before acting
      await api(`/api/v1/alerts/${id}/actions`, {
        method: 'POST', headers: { 'Idempotency-Key': newIdemKey() },
        body: { action, version: fresh.version, note: note || null, assignee: action === 'assign' ? assignee : null },
      })
      setNote(''); setMsg({ text: `${DT2[action].text}: done` })
      await load(); onChanged()
    } catch (e) {
      setMsg({ err: true, text: `${e.status}: ${e.message}` })
    } finally { setBusy(false) }
  }

  if (!a) return <div className="drawer">{msg ? <div className="err">{msg.text}</div> : 'Loading…'}</div>
  return (
    <div className="drawer">
      <div className="row"><Sev s={a.severity} /><b style={{ fontSize: 15 }}>{label(a.type)}</b><Status s={a.status} /><span className="grow" /><button className="btn" onClick={onClose}>×</button></div>
      <div className="kv" style={{ margin: '10px 0' }}>
        <div>Vehicle</div><div>{a.vehicle.name} ({a.vehicle_id}) · run {a.vehicle.run_id}</div>
        <div>Detected</div><div>{fmtTime(a.event_ts)} ({ago(a.event_ts)} ago)</div>
        <div>Model</div><div>{a.model_name} v{a.model_version} · confidence {a.confidence?.toFixed(2)}</div>
        <div>Assignee</div><div>{a.assignee || '—'}</div>
        <div>Version</div><div>{a.version}</div>
      </div>
      <h4>Evidence</h4>
      {ev.frame ? <img src={ev.frame} alt="camera frame" style={{ width: '100%', borderRadius: 6, border: '1px solid var(--line)' }} /> : <div className="muted">No camera frame.</div>}
      {ev.audio && <audio controls src={ev.audio} style={{ width: '100%', marginTop: 6 }} />}
      <div className="muted" style={{ fontSize: 11 }}>Evidence links are presigned S3 URLs that expire after 300 s.</div>
      {canAct && (
        <>
          <h4 style={{ marginTop: 14 }}>Actions</h4>
          <label className="field">Note (required for dispatch, resolve, false alarm)
            <textarea rows="2" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Tow requested, no injuries" />
          </label>
          {DT2.assign.from.includes(a.status) && (
            <label className="field" style={{ marginTop: 6 }}>Assign to
              <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                <option value="">— select —</option>
                {users.map((u) => <option key={u.email} value={u.email}>{u.name} ({label(u.role)})</option>)}
              </select>
            </label>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            {Object.entries(DT2).map(([k, r]) => (
              <button key={k} className={`btn ${k === 'false_alarm' ? 'd' : 'p'}`} disabled={busy || !r.from.includes(a.status)} onClick={() => act(k)}>{r.text}</button>
            ))}
          </div>
        </>
      )}
      {msg && <div className={msg.err ? 'err' : 'stale'} style={{ marginTop: 8 }}>{msg.text}</div>}
      <h4 style={{ marginTop: 14 }}>History</h4>
      <table><tbody>
        <tr><td>{fmtTime(a.created_at)}</td><td>created</td><td className="muted">alert module</td></tr>
        {a.history.map((h, i) => (
          <tr key={i}><td>{fmtTime(h.at)}</td><td>{label(h.action)} → {label(h.to_status)}</td><td>{h.actor}{h.note ? <div className="muted">“{h.note}”</div> : null}</td></tr>
        ))}
      </tbody></table>
    </div>
  )
}

export default function Alerts({ me }) {
  const [params, setParams] = useSearchParams()
  const [status, setStatus] = useState(['open', 'acknowledged', 'assigned', 'dispatched'])
  const [severity, setSeverity] = useState([])
  const [type, setType] = useState('')
  const [sort, setSort] = useState('-created_at')
  const [cursor, setCursor] = useState(null)
  const sel = params.get('id')
  const list = usePoll(() => api(`/api/v1/alerts${qs({ status, severity, type: type ? [type] : [], sort, limit: 25, cursor })}`),
    [status.join(), severity.join(), type, sort, cursor], { report: true })
  const d = list.data

  return (
    <>
      <div className="row"><h2 style={{ margin: 0, fontSize: 18 }}>Alerts</h2><span className="muted">{d?.total ?? '…'} matching</span></div>
      <div className="card">
        <div className="row"><b style={{ width: 60 }}>Status</b><Toggle values={STATUSES} sel={status} setSel={(v) => { setStatus(v); setCursor(null) }} /></div>
        <div className="row" style={{ marginTop: 6 }}>
          <b style={{ width: 60 }}>Severity</b><Toggle values={SEVERITIES} sel={severity} setSel={(v) => { setSeverity(v); setCursor(null) }} />
          <span className="grow" />
          <select value={type} onChange={(e) => { setType(e.target.value); setCursor(null) }}><option value="">All types</option>{TYPES.map((t) => <option key={t} value={t}>{label(t)}</option>)}</select>
          <select value={sort} onChange={(e) => setSort(e.target.value)}><option value="-created_at">Newest first</option><option value="-severity">Severity</option><option value="age">Oldest first</option></select>
        </div>
      </div>
      <div className="card">
        <table>
          <thead><tr><th>Severity</th><th>Type</th><th>Vehicle</th><th>Model</th><th>Conf.</th><th>Created</th><th>Status</th><th>Assignee</th></tr></thead>
          <tbody>
            {(d?.items || []).map((a) => (
              <tr key={a.alert_id} className={`click ${a.alert_id === sel ? 'sel' : ''}`} onClick={() => setParams({ id: a.alert_id })}>
                <td><Sev s={a.severity} /></td><td>{label(a.type)}</td><td>{a.vehicle_id}</td><td>{a.model_name} v{a.model_version}</td>
                <td>{a.confidence?.toFixed(2)}</td><td>{ago(a.created_at)} ago</td><td><Status s={a.status} /></td><td>{a.assignee || '—'}</td>
              </tr>
            ))}
            {d && !d.items.length && <tr><td colSpan="8" className="muted">No alerts match the filters.</td></tr>}
          </tbody>
        </table>
        <div className="row" style={{ marginTop: 8 }}>
          <span className="grow" />
          <button className="btn" disabled={!cursor} onClick={() => setCursor(Math.max(0, Number(cursor) - 25) || null)}>← Previous</button>
          <button className="btn" disabled={!d?.next_cursor} onClick={() => setCursor(d.next_cursor)}>Next →</button>
        </div>
      </div>
      {sel && <Drawer id={sel} me={me} onClose={() => setParams({})} onChanged={list.refresh} />}
    </>
  )
}
