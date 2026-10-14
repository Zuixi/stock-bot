"""Test fixtures and mock in-memory dependencies."""

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.core.redis import get_redis
from app.main import create_app
from app.models.rbac import AuthPermission, AuthRole, AuthRolePermission
from app.models.user import AuthUser


class FakeRedisPipeline:
    """In-memory pipeline for FakeRedis."""

    def __init__(self, fake_redis: "FakeRedis") -> None:
        self.fake_redis = fake_redis
        self.commands: list[tuple[Any, tuple, dict]] = []

    def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        mapping: dict | None = None,
    ) -> "FakeRedisPipeline":
        self.commands.append((self.fake_redis.hset, (name, key, value, mapping), {}))
        return self

    def expire(self, name: str, time_sec: int) -> "FakeRedisPipeline":
        self.commands.append((self.fake_redis.expire, (name, time_sec), {}))
        return self

    def sadd(self, name: str, *values: str) -> "FakeRedisPipeline":
        self.commands.append((self.fake_redis.sadd, (name, *values), {}))
        return self

    def srem(self, name: str, *values: str) -> "FakeRedisPipeline":
        self.commands.append((self.fake_redis.srem, (name, *values), {}))
        return self

    def delete(self, *names: str) -> "FakeRedisPipeline":
        self.commands.append((self.fake_redis.delete, names, {}))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for func, args, kwargs in self.commands:
            res = await func(*args, **kwargs)
            results.append(res)
        self.commands.clear()
        return results


class FakeRedis:
    """In-memory async Redis stand-in for isolated unit and integration testing."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        self._sets: dict[str, set[str]] = {}
        self._ttls: dict[str, float] = {}

    def pipeline(self) -> FakeRedisPipeline:
        return FakeRedisPipeline(self)

    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        if name not in self._hashes:
            self._hashes[name] = {}
        count = 0
        if mapping:
            for k, v in mapping.items():
                self._hashes[name][k] = str(v)
                count += 1
        elif key is not None and value is not None:
            self._hashes[name][key] = str(value)
            count = 1
        return count

    async def hget(self, name: str, key: str) -> str | None:
        return self._hashes.get(name, {}).get(key)

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))

    async def sadd(self, name: str, *values: str) -> int:
        if name not in self._sets:
            self._sets[name] = set()
        prev_len = len(self._sets[name])
        for v in values:
            self._sets[name].add(str(v))
        return len(self._sets[name]) - prev_len

    async def srem(self, name: str, *values: str) -> int:
        if name not in self._sets:
            return 0
        removed = 0
        for v in values:
            if str(v) in self._sets[name]:
                self._sets[name].remove(str(v))
                removed += 1
        return removed

    async def smembers(self, name: str) -> set[str]:
        return set(self._sets.get(name, set()))

    async def expire(self, name: str, time_sec: int) -> bool:
        self._ttls[name] = time.time() + time_sec
        return True

    async def ttl(self, name: str) -> int:
        if name not in self._hashes and name not in self._sets:
            return -2
        exp = self._ttls.get(name)
        if exp is None:
            return -1
        remaining = int(exp - time.time())
        return max(0, remaining)

    async def delete(self, *names: str) -> int:
        count = 0
        for name in names:
            if name in self._hashes:
                del self._hashes[name]
                count += 1
            if name in self._sets:
                del self._sets[name]
                count += 1
            if name in self._ttls:
                del self._ttls[name]
        return count

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        """redis-py 的 ``close()`` 是 ``aclose()`` 的别名；生产路径（cli/close_redis）用的是它。"""
        await self.aclose()

    async def aclose(self) -> None:
        self._hashes.clear()
        self._sets.clear()
        self._ttls.clear()


@pytest_asyncio.fixture(scope="session")
def event_loop() -> AsyncGenerator[asyncio.AbstractEventLoop, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def fake_redis() -> AsyncGenerator[FakeRedis, None]:
    redis = FakeRedis()
    yield redis
    await redis.aclose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite async session with all tables initialized."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        # Seed default roles and permissions in test DB
        roles = [
            AuthRole(id="admin", name="系统管理员", description="全量权限", is_system=True),
            AuthRole(id="researcher", name="专业投研员", description="投研权限", is_system=True),
            AuthRole(id="analyst", name="分析师", description="投研权限", is_system=True),
            AuthRole(id="trader", name="普通交易员", description="交易权限", is_system=True),
            AuthRole(id="viewer", name="只读访客", description="只读权限", is_system=True),
            # 仅测试用：只持 roles:manage，用来区分"挂了守卫"与"挂对了守卫"
            AuthRole(id="roles_only", name="角色管理员", description="仅可改角色", is_system=True),
        ]
        perms = [
            AuthPermission(id="stocks:read", module="stocks", name="查看股票行情"),
            AuthPermission(id="market:read", module="market", name="查看市场与行业"),
            AuthPermission(id="research:read", module="research", name="查看投研工作台"),
            AuthPermission(id="watchlists:write", module="watchlists", name="管理个人自选股"),
            AuthPermission(id="tags:write", module="tags", name="编辑用户自定义标签"),
            AuthPermission(id="tasks:read", module="tasks", name="查看后台任务"),
            AuthPermission(id="tasks:trigger", module="tasks", name="触发后台任务"),
            AuthPermission(id="users:manage", module="admin", name="管理用户账户"),
            AuthPermission(id="roles:manage", module="admin", name="分配角色权限"),
            AuthPermission(id="audit:read", module="admin", name="查看审计日志"),
        ]
        session.add_all(roles)
        session.add_all(perms)
        await session.flush()

        role_perms = [
            # Admin gets all
            AuthRolePermission(role_id="admin", permission_id="stocks:read"),
            AuthRolePermission(role_id="admin", permission_id="market:read"),
            AuthRolePermission(role_id="admin", permission_id="research:read"),
            AuthRolePermission(role_id="admin", permission_id="watchlists:write"),
            AuthRolePermission(role_id="admin", permission_id="tags:write"),
            AuthRolePermission(role_id="admin", permission_id="tasks:read"),
            AuthRolePermission(role_id="admin", permission_id="tasks:trigger"),
            AuthRolePermission(role_id="admin", permission_id="users:manage"),
            AuthRolePermission(role_id="admin", permission_id="roles:manage"),
            AuthRolePermission(role_id="admin", permission_id="audit:read"),
            # Analyst (research tier)
            AuthRolePermission(role_id="analyst", permission_id="stocks:read"),
            AuthRolePermission(role_id="analyst", permission_id="market:read"),
            AuthRolePermission(role_id="analyst", permission_id="research:read"),
            # Trader
            AuthRolePermission(role_id="trader", permission_id="stocks:read"),
            AuthRolePermission(role_id="trader", permission_id="market:read"),
            AuthRolePermission(role_id="trader", permission_id="watchlists:write"),
            # roles_only (probe role: holds roles:manage and nothing else)
            AuthRolePermission(role_id="roles_only", permission_id="roles:manage"),
            # Viewer
            AuthRolePermission(role_id="viewer", permission_id="stocks:read"),
            AuthRolePermission(role_id="viewer", permission_id="market:read"),
        ]
        session.add_all(role_perms)
        await session.commit()

        yield session

    await test_engine.dispose()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    fake_redis: FakeRedis,
) -> AsyncGenerator[AsyncClient, None]:
    """Test client with overridden database and redis dependencies."""
    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[FakeRedis, None]:
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    email: str | None = None,
    password: str = "Password123!",
    status: str = "active",
    status_reason: str | None = None,
    roles: tuple[str, ...] = ("trader",),
    must_change_password: bool = False,
    temp_password_expires_at=None,
) -> AuthUser:
    """Direct-DB user factory for tests (replaces POST /auth/register fixtures).

    The register endpoint is being retired (ADR 0010, invite-only); tests
    seed users here instead.
    """
    import uuid as _uuid

    from app.core.crypto import hash_password
    from app.models.rbac import AuthUserRole
    from app.models.user import AuthCredential, AuthUser

    user = AuthUser(
        id=_uuid.uuid4(),
        username=username,
        email=email or f"{username}@test.local",
        display_name=username,
        status=status,
        status_reason=status_reason,
        is_superuser=False,
        failed_attempts=0,
    )
    db.add(user)
    await db.flush()
    db.add(
        AuthCredential(
            id=_uuid.uuid4(),
            user_id=user.id,
            auth_type="password",
            password_hash=hash_password(password),
            must_change_password=must_change_password,
            temp_password_expires_at=temp_password_expires_at,
        )
    )
    for role_id in roles:
        db.add(AuthUserRole(user_id=user.id, role_id=role_id))
    await db.commit()
    await db.refresh(user, attribute_names=["credentials", "user_roles"])
    return user
