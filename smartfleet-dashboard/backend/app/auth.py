"""Authentication and AuthContext (tenant, role, vehicle scope).

AUTH_MODE=cognito : verifies the Cognito ID token (RS256, JWKS); role from cognito:groups,
                    tenant from custom:tenant_id.
AUTH_MODE=local   : verifies HS256 dev tokens issued by POST /api/v1/dev/login (laptop testing only).
"""
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import text

from . import config
from .db import tx
from .rules import PERMISSIONS, ROLE_ORDER

_jwks = None


def _jwks_client():
    global _jwks
    if _jwks is None:
        url = (f"https://cognito-idp.{config.AWS_REGION}.amazonaws.com/"
               f"{config.COGNITO_USER_POOL_ID}/.well-known/jwks.json")
        _jwks = jwt.PyJWKClient(url, cache_keys=True)
    return _jwks


def decode_token(token: str) -> dict:
    if config.AUTH_MODE == "cognito":
        key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=config.COGNITO_CLIENT_ID,
            issuer=f"https://cognito-idp.{config.AWS_REGION}.amazonaws.com/{config.COGNITO_USER_POOL_ID}")
        if claims.get("token_use") != "id":
            raise jwt.InvalidTokenError("ID token required")
        return claims
    return jwt.decode(token, config.DEV_SECRET, algorithms=["HS256"], audience="smartfleet-local")


def issue_dev_token(email: str, name: str, role: str, tenant_id: str) -> str:
    import time
    now = int(time.time())
    return jwt.encode({"sub": email, "email": email, "name": name, "cognito:groups": [role],
                       "custom:tenant_id": tenant_id, "aud": "smartfleet-local", "token_use": "id",
                       "iat": now, "exp": now + 8 * 3600}, config.DEV_SECRET, algorithm="HS256")


@dataclass
class AuthContext:
    email: str
    name: str
    role: str
    home_tenant: str
    tenant_id: str
    vehicle_scope: list[str] | None = None        # None = all vehicles of the tenant
    perms: set[str] = field(default_factory=set)

    def require(self, perm: str):
        if perm not in self.perms:
            raise HTTPException(403, f"forbidden: role {self.role} lacks {perm}")

    def tx(self):
        return tx(self.tenant_id, self.role)


def get_ctx(request: Request, authorization: str = Header(default=""),
            x_tenant_id: str | None = Header(default=None)) -> AuthContext:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = decode_token(authorization.split(" ", 1)[1])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(401, f"invalid token: {e}")
    groups = claims.get("cognito:groups") or []
    role = next((r for r in ROLE_ORDER if r in groups), None)
    home = claims.get("custom:tenant_id")
    if not role or not home:
        raise HTTPException(403, "user has no role group or tenant_id")
    tenant = home
    if x_tenant_id and x_tenant_id != home:                     # BR-1: only platform admins may switch
        if role != "platform_admin":
            raise HTTPException(403, "tenant switch not allowed")
        tenant = x_tenant_id
    email = claims.get("email") or claims.get("sub")
    ctx = AuthContext(email=email, name=claims.get("name") or email, role=role, home_tenant=home,
                      tenant_id=tenant, perms=PERMISSIONS[role])
    with ctx.tx() as conn:
        st = conn.execute(text("SELECT status FROM tenant WHERE tenant_id=:t"), {"t": tenant}).scalar()
        if st is None:
            raise HTTPException(403, "unknown tenant")
        if st != "active":                                      # BR-7
            raise HTTPException(403, "tenant suspended")
        if role == "vehicle_owner":                              # BR-2
            ctx.vehicle_scope = [r[0] for r in conn.execute(
                text("SELECT vehicle_id FROM vehicle WHERE tenant_id=:t AND owner_email=:e"),
                {"t": tenant, "e": email})]
    request.state.ctx = ctx
    return ctx


def require(perm: str):
    def dep(ctx: AuthContext = Depends(get_ctx)) -> AuthContext:
        ctx.require(perm)
        return ctx
    return dep


def sim_key(x_sim_key: str = Header(default="")):
    """Shared key for the simulator / alert-module stub endpoints."""
    if x_sim_key != config.SIM_API_KEY:
        raise HTTPException(401, "bad simulator key")
    return True
