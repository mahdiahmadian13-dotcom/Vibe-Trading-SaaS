"""Vibe-Trading SaaS — definable admin roles (US-12, FR-024).

is_admin stays the superuser bypass. Non-admin users may hold roles
(admin_role_links) with permission flags; require_perm("servers")
raises 403 (Persian) unless the user is_admin or holds the flag.
Security is enforced server-side — UI hiding alone is never enough.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import AdminRole, AdminRoleLink, User

VALID_PERMS = {"dashboard", "servers", "users", "secrets", "updates"}


async def user_perms(db: AsyncSession, user: User) -> set[str]:
    if user.is_admin:
        return set(VALID_PERMS)
    rows = (
        await db.execute(
            select(AdminRole.perms).join(AdminRoleLink, AdminRoleLink.role_id == AdminRole.id).where(
                AdminRoleLink.user_id == user.id
            )
        )
    ).all()
    out: set[str] = set()
    for (perms,) in rows:
        if isinstance(perms, dict):
            out.update(k for k, v in perms.items() if v and k in VALID_PERMS)
    return out


def require_perm(perm: str, require_auth_dep, get_db_dep):
    """Build the FastAPI dependency: auth + perm check. Used in main.py.

    Usage:  admin_servers = require_perm("servers", require_auth, get_db)
            @app.get(..., dependencies=[Depends(admin_servers)])
    """
    if perm not in VALID_PERMS:
        raise ValueError(f"unknown perm: {perm}")

    async def _dep(
        db: AsyncSession = Depends(get_db_dep),
        user: User = Depends(require_auth_dep),
    ) -> User:
        if user.is_admin:
            return user
        if perm in await user_perms(db, user):
            return user
        raise HTTPException(403, "دسترسی کافی ندارید")

    return _dep
