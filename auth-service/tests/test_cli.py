"""bootstrap-admin CLI tests: three-state idempotency (ADR 0010).

The bootstrap is a one-way door: if it silently reset passwords on re-run it
would be a backdoor. These tests lock the contract:
- empty DB            -> creates admin
- existing non-admin  -> adds admin role only, password untouched
- existing admin      -> zero changes
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.rbac import AuthUserRole
from app.models.user import AuthUser
from tests.conftest import create_user


async def _count_admins(db) -> int:
    res = await db.execute(select(AuthUserRole).where(AuthUserRole.role_id == "admin"))
    return len(res.scalars().all())


@pytest.mark.asyncio
async def test_bootstrap_creates_first_admin(db_session, fake_redis, monkeypatch) -> None:
    from app import cli

    # point the CLI's session factory at the test DB
    monkeypatch.setattr(cli, "make_session_factory", lambda: _TestSessionFactory(db_session))
    monkeypatch.setattr("app.core.redis.get_redis_client", lambda: fake_redis)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "BootstrapPass123!")

    rc = await cli.main_async(
        ["bootstrap-admin", "--username", "first_admin", "--email", "fa@example.com"]
    )
    assert rc == 0

    user = (
        await db_session.execute(select(AuthUser).where(AuthUser.username == "first_admin"))
    ).scalar_one()
    roles = (
        (await db_session.execute(select(AuthUserRole).where(AuthUserRole.user_id == user.id)))
        .scalars()
        .all()
    )
    assert "admin" in {r.role_id for r in roles}
    assert user.status == "active"


@pytest.mark.asyncio
async def test_bootstrap_existing_user_only_adds_role(db_session, fake_redis, monkeypatch) -> None:
    from app import cli

    user = await create_user(db_session, username="existing_op", password="KeepThis123!")
    before_hash = user.credentials[0].password_hash
    before_updated = user.credentials[0].password_updated_at

    monkeypatch.setattr(cli, "make_session_factory", lambda: _TestSessionFactory(db_session))
    monkeypatch.setattr("app.core.redis.get_redis_client", lambda: fake_redis)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "ShouldBeIgnored123!")

    rc = await cli.main_async(
        ["bootstrap-admin", "--username", "existing_op", "--email", "any@example.com"]
    )
    assert rc == 0

    await db_session.refresh(user, attribute_names=["credentials", "user_roles"])
    assert user.credentials[0].password_hash == before_hash, "password must never be reset"
    assert user.credentials[0].password_updated_at == before_updated
    assert "admin" in {ur.role_id for ur in user.user_roles}


@pytest.mark.asyncio
async def test_bootstrap_existing_admin_zero_changes(db_session, fake_redis, monkeypatch) -> None:
    from app import cli

    user = await create_user(
        db_session, username="already_admin", password="Untouched123!", roles=("admin",)
    )
    before_hash = user.credentials[0].password_hash

    monkeypatch.setattr(cli, "make_session_factory", lambda: _TestSessionFactory(db_session))
    monkeypatch.setattr("app.core.redis.get_redis_client", lambda: fake_redis)
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "ShouldBeIgnored123!")

    rc = await cli.main_async(
        ["bootstrap-admin", "--username", "already_admin", "--email", "x@example.com"]
    )
    assert rc == 0

    await db_session.refresh(user, attribute_names=["credentials", "user_roles"])
    assert user.credentials[0].password_hash == before_hash
    admin_count = await _count_admins(db_session)
    assert admin_count == 1, "no duplicate admin role rows"


class _TestSessionFactory:
    """Minimal async context manager mimicking async_sessionmaker for tests."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False


def test_bootstrap_admin_defaults_to_platform_identity() -> None:
    """无参数即为平台标准管理员身份 freedom / zeebest@freedom.com。"""
    from app import cli

    parser_args: dict = {}

    async def fake_run(username: str, email: str, password: str) -> int:
        parser_args.update(username=username, email=email, password=password)
        return 0

    import app.cli as cli_module

    original = cli_module._run_bootstrap
    cli_module._run_bootstrap = fake_run  # type: ignore[assignment]
    try:
        import asyncio

        rc = asyncio.run(cli.main_async(["bootstrap-admin"]))
    finally:
        cli_module._run_bootstrap = original  # type: ignore[assignment]

    assert rc == 0
    assert parser_args["username"] == "freedom"
    assert parser_args["email"] == "zeebest@freedom.com"
