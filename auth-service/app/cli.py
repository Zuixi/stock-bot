"""Bootstrap CLI for the first admin account (ADR 0010).

Usage:
    python -m app.cli bootstrap-admin [--username <name>] [--email <mail>]

Defaults to the platform's standard admin identity (freedom /
zeebest@freedom.com) so the usual invocation needs no arguments.

Password comes from ADMIN_BOOTSTRAP_PASSWORD or an interactive prompt —
never from argv (it would land in shell history and `ps`), and never from a
hard-coded default: a seeded password is a backdoor, which is exactly why
this CLI exists instead of a migration seed.

Idempotent by design: an existing user only gets the admin role added,
their password is NEVER reset. A bootstrap that silently resets passwords
is a backdoor.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.core.database import async_session_factory as make_session_factory
from app.models.rbac import AuthUserRole
from app.models.user import AuthUser
from app.services.admin_service import AdminService
from app.services.session_service import SessionService

# 平台标准管理员身份。只固定"身份"，不固定密码。
DEFAULT_ADMIN_USERNAME = "freedom"
DEFAULT_ADMIN_EMAIL = "zeebest@freedom.com"


async def _run_bootstrap(username: str, email: str, password: str) -> int:
    from app.core.redis import get_redis_client

    session_factory = make_session_factory
    redis = get_redis_client()
    try:
        async with session_factory() as db:
            existing = (
                await db.execute(select(AuthUser).where(AuthUser.username == username))
            ).scalar_one_or_none()

            if existing is not None:
                # Idempotent: only ensure the admin role, never touch the password.
                has_admin = (
                    await db.execute(
                        select(AuthUserRole).where(
                            AuthUserRole.user_id == existing.id,
                            AuthUserRole.role_id == "admin",
                        )
                    )
                ).scalar_one_or_none()
                if has_admin is None:
                    db.add(AuthUserRole(user_id=existing.id, role_id="admin", assigned_by=None))
                    await db.commit()
                    print(f"[bootstrap] 用户 {username} 已存在，已补挂 admin 角色（密码未改动）")
                else:
                    print(f"[bootstrap] 用户 {username} 已拥有 admin 角色，无变更")
                return 0

            service = AdminService(SessionService(redis))
            user, temp_password, expires_at = await service.provision_user(
                db,
                username=username,
                email=email,
                display_name=username,
                roles=["admin"],
                acting_user=None,
            )
            print(f"[bootstrap] 已创建管理员 {username} (id={user.id})")
            if password:
                # Operator supplied the initial password themselves: use it.
                from app.core.crypto import hash_password
                from app.models.user import AuthCredential

                cred = (
                    await db.execute(
                        select(AuthCredential).where(
                            AuthCredential.user_id == user.id,
                            AuthCredential.auth_type == "password",
                        )
                    )
                ).scalar_one()
                cred.password_hash = hash_password(password)
                await db.commit()
                print("[bootstrap] 初始密码已按 ADMIN_BOOTSTRAP_PASSWORD 设置；首登仍会要求修改")
            else:
                print(f"[bootstrap] 一次性初始密码: {temp_password}")
                print(f"[bootstrap] 过期时间: {expires_at.isoformat()}")
                print("[bootstrap] 该密码仅显示这一次，请立即转交并要求首登改密")
            return 0
    finally:
        await redis.close()


async def main_async(argv: list[str] | None = None) -> int:
    """Async entry (used by tests and any already-running loop)."""
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="app.cli", description="auth-service 运维命令")
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser("bootstrap-admin", help="创建/补挂第一个管理员（幂等，不重置密码）")
    boot.add_argument("--username", default=DEFAULT_ADMIN_USERNAME)
    boot.add_argument("--email", default=DEFAULT_ADMIN_EMAIL)

    args = parser.parse_args(argv)

    if args.command == "bootstrap-admin":
        password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "").strip()
        if not password and sys.stdin.isatty():
            password = getpass.getpass("初始密码（不回显，留空则生成一次性临时密码）: ").strip()
            confirm = getpass.getpass("确认密码（直接回车表示使用一次性临时密码）: ").strip()
            if password and password != confirm:
                print("两次输入不一致", file=sys.stderr)
                return 2
            if not password:
                password = ""
        return await _run_bootstrap(args.username, args.email, password)
    return 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    sys.exit(main())
