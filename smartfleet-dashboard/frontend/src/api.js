// ApiClient: bearer token, tenant header (platform admin), ETag cache (304 -> cached data), idempotency keys.
import { getToken, signIn } from './auth'

const etags = new Map()

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status }
}

export async function api(path, { method = 'GET', body, headers = {} } = {}) {
  const token = await getToken()
  if (!token) { await signIn(); throw new ApiError(401, 'signed out') }
  const h = { ...headers, Authorization: `Bearer ${token}` }
  const tenant = sessionStorage.getItem('sf_tenant')
  if (tenant) h['X-Tenant-Id'] = tenant
  if (body !== undefined) h['Content-Type'] = 'application/json'
  const key = `${tenant || ''}:${path}`
  if (method === 'GET' && etags.has(key)) h['If-None-Match'] = etags.get(key).tag
  const res = await fetch(path, { method, headers: h, body: body !== undefined ? JSON.stringify(body) : undefined })
  if (res.status === 304) return etags.get(key).data
  if (res.status === 401) { sessionStorage.removeItem('sf_token'); await signIn(); throw new ApiError(401, 'session expired') }
  const isJson = (res.headers.get('content-type') || '').includes('json')
  const data = isJson ? await res.json() : null
  if (!res.ok) {
    const d = data?.detail
    throw new ApiError(res.status, Array.isArray(d) ? d.join('; ') : d || data?.title || res.statusText)
  }
  const tag = res.headers.get('ETag')
  if (tag && method === 'GET') etags.set(key, { tag, data })
  return data
}

export const qs = (params) => {
  const u = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach((x) => u.append(k, x))
    else if (v !== undefined && v !== null && v !== '') u.append(k, v)
  })
  const s = u.toString()
  return s ? `?${s}` : ''
}

export const newIdemKey = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now() + Math.random()))
