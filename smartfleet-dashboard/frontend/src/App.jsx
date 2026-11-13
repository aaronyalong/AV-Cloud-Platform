// AppShell: sign-in hand-off, role-based navigation (DT-1), tenant switcher (platform admin), live indicator.
import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api } from './api'
import { completeSignIn, config, getToken, signIn, signOut } from './auth'
import { LIVE_COLORS } from './components/ui'
import { liveBus } from './usePoll'
import Alerts from './pages/Alerts'
import Dashboard from './pages/Dashboard'
import Health from './pages/Health'
import Login from './pages/Login'
import Models from './pages/Models'
import Statistics from './pages/Statistics'

const NAV = [
  { group: 'Monitor', items: [['dashboard', '/', 'Dashboard'], ['alerts', '/alerts', 'Alerts'], ['statistics', '/statistics', 'Statistics']] },
  { group: 'Engineering', items: [['models', '/models', 'Models']] },
  { group: 'Admin', items: [['health', '/health', 'System health']] },
]
const ROLE_LABEL = {
  platform_admin: 'Platform admin', tenant_admin: 'Tenant admin', fleet_operator: 'Fleet operator',
  support_staff: 'Support staff', vehicle_owner: 'Vehicle owner', ai_engineer: 'AI engineer',
}

function LiveIndicator() {
  const [s, setS] = useState({ state: 'loading', age: 0 })
  useEffect(() => liveBus.subscribe(setS), [])
  const text = { live: 'Live', stale: `Stale · ${s.age} s old`, error: 'Connection error', paused: 'Paused', loading: 'Loading' }[s.state]
  return <span className="pill live" title="Data freshness"><span className="dot" style={{ background: LIVE_COLORS[s.state] }} />{text}</span>
}

function Callback() {
  const nav = useNavigate()
  useEffect(() => { completeSignIn().then(() => nav('/', { replace: true })).catch(() => signIn()) }, [nav])
  return <div className="login card">Completing sign-in…</div>
}

export default function App() {
  const loc = useLocation()
  const [me, setMe] = useState(null)
  const [tenants, setTenants] = useState([])
  const [err, setErr] = useState(null)
  const publicRoute = loc.pathname === '/login' || loc.pathname === '/callback'

  useEffect(() => {
    if (publicRoute) return
    (async () => {
      if (!(await getToken())) return signIn()
      try {
        const m = await api('/api/v1/me')
        setMe(m)
        if (m.permissions.includes('tenants:switch')) setTenants(await api('/api/v1/tenants'))
      } catch (e) { setErr(e.message) }
    })()
  }, [publicRoute])

  if (loc.pathname === '/callback') return <Callback />
  if (loc.pathname === '/login') return config().authMode === 'local' ? <Login /> : <Navigate to="/" />
  if (err) return <div className="login card err">Cannot load profile: {err} <button className="btn" onClick={signOut}>Sign out</button></div>
  if (!me) return <div className="login card">Loading…</div>

  const can = (page) => me.pages.includes(page)
  const switchTenant = (t) => { sessionStorage.setItem('sf_tenant', t); window.location.reload() }
  const home = can('dashboard') ? '/' : `/${me.pages[0]}`

  return (
    <div className="shell">
      <header className="topbar">
        <b>SmartFleet Cloud</b>
        {tenants.length > 0 ? (
          <select className="pill" value={me.tenant_id} onChange={(e) => switchTenant(e.target.value)} title="Switch tenant (audited)">
            {tenants.map((t) => <option key={t.tenant_id} value={t.tenant_id}>Tenant: {t.name}</option>)}
          </select>
        ) : <span className="pill">Tenant: {me.tenant_name}</span>}
        <span className="sp" />
        <LiveIndicator />
        <span>{me.name} · {ROLE_LABEL[me.role]}</span>
        <button className="pill" onClick={signOut}>Sign out</button>
      </header>
      <nav className="nav">
        {NAV.map((g) => {
          const items = g.items.filter(([p]) => can(p))
          if (!items.length) return null
          return (
            <div key={g.group}>
              <div className="grp">{g.group}</div>
              {items.map(([p, to, text]) => <NavLink key={p} to={to} end={to === '/'}>{text}</NavLink>)}
            </div>
          )
        })}
      </nav>
      <main className="main">
        <Routes>
          {can('dashboard') && <Route path="/" element={<Dashboard me={me} />} />}
          {can('alerts') && <Route path="/alerts" element={<Alerts me={me} />} />}
          {can('statistics') && <Route path="/statistics" element={<Statistics />} />}
          {can('models') && <Route path="/models" element={<Models />} />}
          {can('health') && <Route path="/health" element={<Health me={me} />} />}
          <Route path="*" element={<Navigate to={home} replace />} />
        </Routes>
      </main>
    </div>
  )
}
