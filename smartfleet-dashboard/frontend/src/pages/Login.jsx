// Local-only sign-in (AUTH_MODE=local). In AWS the Cognito Hosted UI replaces this page.
import { useEffect, useState } from 'react'
import { devLogin } from '../auth'

export default function Login() {
  const [users, setUsers] = useState([])
  const [err, setErr] = useState(null)
  useEffect(() => { fetch('/api/v1/dev/users').then((r) => r.json()).then(setUsers).catch(() => setErr('API not reachable')) }, [])
  const go = async (email) => {
    try { sessionStorage.clear(); await devLogin(email); window.location.assign('/') } catch (e) { setErr(e.message) }
  }
  return (
    <div className="login card">
      <h2 style={{ marginTop: 0 }}>SmartFleet Cloud</h2>
      <p className="muted">Local development sign-in. Pick a demo user (in AWS this is the Cognito Hosted UI).</p>
      {err && <div className="err">{err}</div>}
      <table>
        <thead><tr><th>User</th><th>Role</th><th>Tenant</th><th /></tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.email}>
              <td>{u.name}<div className="muted">{u.email}</div></td><td>{u.role}</td><td>{u.tenant_id}</td>
              <td><button className="btn p" onClick={() => go(u.email)}>Sign in</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
