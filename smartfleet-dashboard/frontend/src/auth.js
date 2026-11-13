// Authentication: Cognito Hosted UI (authorization code + PKCE via oidc-client-ts) in AWS,
// or dev tokens from the local API when config.json says {"authMode": "local"}.
import { UserManager, WebStorageStateStore } from 'oidc-client-ts'

let cfg = null
let mgr = null

export async function loadConfig() {
  cfg = await (await fetch('/config.json', { cache: 'no-store' })).json()
  if (cfg.authMode === 'cognito') {
    mgr = new UserManager({
      authority: `https://cognito-idp.${cfg.region}.amazonaws.com/${cfg.userPoolId}`,
      client_id: cfg.clientId,
      redirect_uri: `${window.location.origin}/callback`,
      post_logout_redirect_uri: `${window.location.origin}/`,
      response_type: 'code',
      scope: 'openid email profile',
      automaticSilentRenew: true, // uses the refresh token before the 1 h ID token expires
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    })
  }
  return cfg
}

export const config = () => cfg

export async function getToken() {
  if (cfg.authMode === 'cognito') {
    const user = await mgr.getUser()
    return user && !user.expired ? user.id_token : null
  }
  return sessionStorage.getItem('sf_token')
}

export async function signIn() {
  if (cfg.authMode === 'cognito') return mgr.signinRedirect()
  window.location.assign('/login')
}

export async function completeSignIn() {
  await mgr.signinRedirectCallback()
}

export async function devLogin(email) {
  const r = await fetch('/api/v1/dev/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }),
  })
  if (!r.ok) throw new Error('login failed')
  sessionStorage.setItem('sf_token', (await r.json()).id_token)
}

export async function signOut() {
  sessionStorage.clear()
  if (cfg.authMode === 'cognito') {
    await mgr.removeUser()
    const q = new URLSearchParams({ client_id: cfg.clientId, logout_uri: `${window.location.origin}/` })
    window.location.assign(`https://${cfg.cognitoDomain}/logout?${q}`)
  } else {
    window.location.assign('/login')
  }
}
