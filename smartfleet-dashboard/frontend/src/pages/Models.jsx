// Model management (AI component): registry, register + upload artifact, deploy / roll back, deployment history.
import { useState } from 'react'
import { api } from '../api'
import { fmtTime, Status } from '../components/ui'
import { usePoll } from '../usePoll'

const EMPTY = { name: 'audio-hazard', version: '', framework: 'CNN-MFCC', precision: '', recall: '', threshold: '0.6', notes: '' }

export default function Models() {
  const m = usePoll(() => api('/api/v1/models'), [], { interval: 5000, report: true })
  const [form, setForm] = useState(EMPTY)
  const [file, setFile] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [msg, setMsg] = useState(null)
  const d = m.data
  const can = d?.can_write
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  const register = async (e) => {
    e.preventDefault(); setMsg(null)
    try {
      const r = await api('/api/v1/models', { method: 'POST', body: { ...form, precision: +form.precision, recall: +form.recall, threshold: +form.threshold } })
      if (file) await fetch(r.upload_url, { method: 'PUT', body: file, headers: { 'Content-Type': 'application/octet-stream' } })
      setMsg({ text: `Registered ${form.name} v${form.version}${file ? ' and uploaded the artifact to S3' : ''}.` })
      setForm(EMPTY); setFile(null); m.refresh()
    } catch (err) { setMsg({ err: true, text: err.message }) }
  }
  const deploy = async (mv) => {
    setConfirm(null); setMsg(null)
    try {
      await api(`/api/v1/models/${mv.model_id}/deploy`, { method: 'POST' })
      setMsg({ text: `${mv.name} v${mv.version} is now ACTIVE. New detections use it (threshold ${mv.threshold}).` })
      m.refresh()
    } catch (err) { setMsg({ err: true, text: err.message }) }
  }

  const groups = {}
  ;(d?.models || []).forEach((x) => { (groups[x.name] = groups[x.name] || []).push(x) })

  return (
    <>
      <div className="row"><h2 style={{ margin: 0, fontSize: 18 }}>Model management</h2><span className="muted">registry · deployment · rollback</span></div>
      {msg && <div className={msg.err ? 'err' : 'stale'}>{msg.text}</div>}
      {confirm && (
        <div className="banner">
          <span>{confirm.status === 'retired' ? 'Roll back to' : 'Deploy'} <b>{confirm.name} v{confirm.version}</b>? The current active version will be retired and new alerts will use threshold {confirm.threshold}.</span>
          <span className="grow" /><button className="btn p" onClick={() => deploy(confirm)}>Confirm</button><button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
        </div>
      )}
      {Object.entries(groups).map(([name, list]) => (
        <div key={name} className="card">
          <h4>{name}</h4>
          <table>
            <thead><tr><th>Version</th><th>Framework</th><th>Precision</th><th>Recall</th><th>F1</th><th>Threshold</th><th>Status</th><th>Deployed</th><th /></tr></thead>
            <tbody>
              {list.map((x) => (
                <tr key={x.model_id} style={x.status === 'active' ? { background: '#effaef' } : undefined}>
                  <td><b>v{x.version}</b></td><td>{x.framework}</td><td>{x.metrics.precision}</td><td>{x.metrics.recall}</td><td>{x.metrics.f1}</td>
                  <td>{x.threshold}</td><td><Status s={x.status} /></td>
                  <td>{x.deployed_at ? <>{fmtTime(x.deployed_at)}<div className="muted">{x.deployed_by}</div></> : '—'}</td>
                  <td>{can && x.status !== 'active' && <button className="btn p" onClick={() => setConfirm(x)}>{x.status === 'retired' ? 'Roll back' : 'Deploy'}</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <div className="grid2e">
        {can && (
          <form className="card" onSubmit={register}>
            <h4>Register a new model version</h4>
            <div className="grid2e">
              <label className="field">Model<select value={form.name} onChange={set('name')}><option>audio-hazard</option><option>vision-hazard</option></select></label>
              <label className="field">Version<input required placeholder="1.4" value={form.version} onChange={set('version')} /></label>
              <label className="field">Framework<input required value={form.framework} onChange={set('framework')} /></label>
              <label className="field">Alert threshold<input required type="number" step="0.01" min="0.05" max="0.99" value={form.threshold} onChange={set('threshold')} /></label>
              <label className="field">Precision<input required type="number" step="0.01" min="0" max="1" value={form.precision} onChange={set('precision')} /></label>
              <label className="field">Recall<input required type="number" step="0.01" min="0" max="1" value={form.recall} onChange={set('recall')} /></label>
            </div>
            <label className="field" style={{ marginTop: 6 }}>Model artifact (optional, uploaded to S3)<input type="file" onChange={(e) => setFile(e.target.files[0])} /></label>
            <label className="field" style={{ marginTop: 6 }}>Notes<input value={form.notes} onChange={set('notes')} /></label>
            <div className="row" style={{ marginTop: 8 }}><span className="grow" /><button className="btn p" type="submit">Register</button></div>
          </form>
        )}
        <div className="card">
          <h4>Deployment history</h4>
          <table><tbody>
            {(d?.events || []).map((e, i) => <tr key={i}><td>{fmtTime(e.at)}</td><td>{e.action.replace(/_/g, ' ')}</td><td>{e.name} v{e.version}</td><td>{e.actor}</td></tr>)}
            {d && !d.events.length && <tr><td className="muted">No deployments yet.</td></tr>}
          </tbody></table>
          {!can && <p className="muted">Read-only: only AI engineers can register or deploy models.</p>}
        </div>
      </div>
    </>
  )
}
