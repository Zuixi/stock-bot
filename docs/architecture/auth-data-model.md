# 股票数据分析系统 - 认证数据模型与安全存储设计

> 文档版本：1.0 | 实施阶段：Stage 0 | 生效日期：2026-09-09  
> 目标系统：auth-service 独立数据库模型、安全凭证算法、会话存储与业务数据归属改造规范

---

## 1. auth-service 独立数据库模型设计

`auth-service` 采用专有数据库（或独立 Schema `auth`），实现认证身份数据与业务行情数据的物理/逻辑解耦。所有表采用 `auth_` 前缀，主键统一采用 UUIDv4 格式，时间字段采用带时区的 `TIMESTAMPTZ`。

### 1.1 ER 实体关系图

```
 ┌─────────────────────────┐       1:1      ┌─────────────────────────┐
 │       auth_users        ├────────────────┤    auth_credentials     │
 │ (id, email, username...)│                │ (user_id, password_hash)│
 └───────────┬─────────────┘                └─────────────────────────┘
             │ 1:N
             ├──────────────────────────────┬─────────────────────────┐
             │ 1:N                          │ 1:N                     │ 1:N
             ▼                              ▼                         ▼
┌─────────────────────────┐   ┌───────────────────────────┐ ┌─────────────────────────┐
│      auth_sessions      │   │auth_refresh_token_families│ │    auth_audit_events    │
│(id, user_id, ip, ua...) │   │ (id, family_id, user_id)  │ │ (id, user_id, action...)│
└─────────────────────────┘   └───────────────────────────┘ └─────────────────────────┘
             │ N:M
             ▼
┌─────────────────────────┐       N:M       ┌─────────────────────────┐
│     auth_user_roles     │◄────────────────┤       auth_roles        │
│   (user_id, role_id)    │                 │ (id, name, description) │
└─────────────────────────┘                 └────────────┬────────────┘
                                                         │ 1:N (JSONB)
                                                         ▼
                                            ┌─────────────────────────┐
                                            │    auth_permissions     │
                                            │ (id, code, description) │
                                            └─────────────────────────┘
```

### 1.2 DDL 定义

```sql
-- 1. 用户主表
CREATE TABLE auth_users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(64) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    display_name    VARCHAR(64),
    status          VARCHAR(32) NOT NULL DEFAULT 'active', -- active, suspended, locked, pending_verification
    is_superuser    BOOLEAN NOT NULL DEFAULT FALSE,
    failed_attempts INT NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_auth_users_username UNIQUE (username),
    CONSTRAINT uq_auth_users_email UNIQUE (email),
    CONSTRAINT chk_auth_user_status CHECK (status IN ('active', 'suspended', 'locked', 'pending_verification'))
);

CREATE INDEX idx_auth_users_status ON auth_users(status);
CREATE INDEX idx_auth_users_created_at ON auth_users(created_at DESC);

COMMENT ON TABLE auth_users IS '用户核心账号表';

-- 2. 用户凭据表 (密码与其他认证方式解耦)
CREATE TABLE auth_credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    auth_type       VARCHAR(32) NOT NULL DEFAULT 'password', -- password, totp, oauth_github
    password_hash   TEXT,                                    -- Argon2id 格式编码串
    salt            VARCHAR(64),                             -- 额外安全盐 (可选)
    password_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_auth_credentials_user FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    CONSTRAINT uq_auth_credentials_user_type UNIQUE (user_id, auth_type)
);

CREATE INDEX idx_auth_credentials_user_id ON auth_credentials(user_id);

COMMENT ON TABLE auth_credentials IS '用户敏感认证凭据表，与主表 1:1 或 1:N 物理隔离';

-- 3. 角色定义表
CREATE TABLE auth_roles (
    id              VARCHAR(32) PRIMARY KEY, -- 如 'admin', 'trader', 'viewer', 'researcher'
    name            VARCHAR(64) NOT NULL,
    description     VARCHAR(255),
    is_system       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO auth_roles (id, name, description, is_system) VALUES
('admin', '系统管理员', '拥有系统全量管理权限与任务调度权限', TRUE),
('researcher', '专业投研员', '具备数据查看、自定义标签、投研工作台配置、个人自选权限', TRUE),
('trader', '普通交易员', '具备股票数据与自选股基础功能', TRUE),
('viewer', '只读访客', '仅具备公开行情数据查看权限', TRUE);

-- 4. 细粒度权限项表
CREATE TABLE auth_permissions (
    id              VARCHAR(64) PRIMARY KEY, -- 如 'stocks:read', 'tasks:trigger', 'watchlists:write'
    module          VARCHAR(32) NOT NULL,    -- 'stocks', 'tasks', 'admin', 'watchlists'
    name            VARCHAR(64) NOT NULL,
    description     VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO auth_permissions (id, module, name, description) VALUES
('stocks:read', 'stocks', '查看股票行情', '允许查询个股详情、分时与日K行情'),
('market:read', 'market', '查看市场与行业', '允许查询申万行业与市场概览'),
('research:read', 'research', '查看投研工作台', '允许查看生猪等行业投研工作台'),
('watchlists:write', 'watchlists', '管理个人自选股', '允许新增、编辑、删除个人自选股'),
('tags:write', 'tags', '编辑用户自定义标签', '允许为股票打自定义申万分类标签'),
('tasks:read', 'tasks', '查看后台任务', '允许查看数据采集与回填任务进度'),
('tasks:trigger', 'tasks', '触发后台任务', '允许手动触发采集、回填与聚类计算'),
('users:manage', 'admin', '管理用户账户', '允许创建用户、封禁账户与重置密码'),
('roles:manage', 'admin', '分配角色权限', '允许变更用户角色分配');

-- 5. 角色权限映射表
CREATE TABLE auth_role_permissions (
    role_id         VARCHAR(32) NOT NULL REFERENCES auth_roles(id) ON DELETE CASCADE,
    permission_id   VARCHAR(64) NOT NULL REFERENCES auth_permissions(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (role_id, permission_id)
);

-- 初始化基础角色权限
INSERT INTO auth_role_permissions (role_id, permission_id)
SELECT 'admin', id FROM auth_permissions;

INSERT INTO auth_role_permissions (role_id, permission_id) VALUES
('researcher', 'stocks:read'), ('researcher', 'market:read'), ('researcher', 'research:read'),
('researcher', 'watchlists:write'), ('researcher', 'tags:write'), ('researcher', 'tasks:read'),
('trader', 'stocks:read'), ('trader', 'market:read'), ('trader', 'watchlists:write'),
('viewer', 'stocks:read'), ('viewer', 'market:read');

-- 6. 用户与角色关联表
CREATE TABLE auth_user_roles (
    user_id         UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    role_id         VARCHAR(32) NOT NULL REFERENCES auth_roles(id) ON DELETE CASCADE,
    assigned_by     UUID REFERENCES auth_users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, role_id)
);

CREATE INDEX idx_auth_user_roles_user ON auth_user_roles(user_id);

-- 7. 活跃会话持久化快照表 (配合 Redis 作审计与会话下线)
CREATE TABLE auth_sessions (
    id              VARCHAR(64) PRIMARY KEY, -- sess_<random_32_chars>
    user_id         UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    ip_address      VARCHAR(45),             -- IPv4 或 IPv6
    user_agent      TEXT,
    device_fingerprint VARCHAR(128),
    csrf_token_hash VARCHAR(64) NOT NULL,
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at      TIMESTAMPTZ NOT NULL,
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_auth_sessions_user_id ON auth_sessions(user_id, is_revoked);
CREATE INDEX idx_auth_sessions_expires_at ON auth_sessions(expires_at);

-- 8. Refresh Token 家族轮换表 (用于 OAuth2/移动端/长会话刷新防重放)
CREATE TABLE auth_refresh_token_families (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id       UUID NOT NULL,           -- 家族根标识符 (一次登录派生一族)
    user_id         UUID NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    token_hash      VARCHAR(64) NOT NULL,    -- SHA-256(refresh_token)
    sequence        INT NOT NULL DEFAULT 1,  -- 世代递增计数器
    is_used         BOOLEAN NOT NULL DEFAULT FALSE,
    is_revoked      BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_auth_token_hash UNIQUE (token_hash)
);

CREATE INDEX idx_auth_rt_family ON auth_refresh_token_families(family_id);
CREATE INDEX idx_auth_rt_user ON auth_refresh_token_families(user_id);

-- 9. 安全审计日志表 (不可变日志，只追加不修改)
CREATE TABLE auth_audit_events (
    id              BIGSERIAL PRIMARY KEY,
    event_id        UUID NOT NULL DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES auth_users(id) ON DELETE SET NULL,
    event_type      VARCHAR(64) NOT NULL, -- 'auth.login.success', 'auth.login.failed', 'auth.session.revoked', 'auth.token.replayed', 'admin.role.assigned'
    status          VARCHAR(32) NOT NULL, -- 'SUCCESS', 'FAILED', 'DENIED'
    ip_address      VARCHAR(45),
    user_agent      TEXT,
    trace_id        VARCHAR(64),
    payload         JSONB,                -- 事件上下文 (如失败原因、变更前后对比)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_auth_audit_user_id ON auth_audit_events(user_id, created_at DESC);
CREATE INDEX idx_auth_audit_event_type ON auth_audit_events(event_type, created_at DESC);
CREATE INDEX idx_auth_audit_created_at ON auth_audit_events(created_at DESC);
```

---

## 2. 核心凭证算法与密码安全策略

### 2.1 Argon2id 哈希算法规范

`auth-service` 采用行业最高安全性标准的 **Argon2id**（RFC 9106）算法进行口令哈希，绝不使用已被攻破或抗 GPU/ASIC 算力较弱的 MD5、SHA-256 或低迭代 bcrypt。

#### 参数配置标准：

```python
# auth_service/core/security/password.py 推荐实现参数
from passlib.context import CryptContext
from argon2 import PasswordHasher, Type

argon2_hasher = PasswordHasher(
    time_cost=3,          # 迭代轮数 t=3
    memory_cost=65536,    # 内存占用 m=64MB (65536 KiB)
    parallelism=4,        # 并行线程数 p=4
    hash_len=32,          # 生成哈希长度 32 bytes
    salt_len=16,          # 随机加盐长度 16 bytes
    type=Type.ID          # 混合模式 Argon2id (兼顾抗侧信道与抗 GPU 暴力破解)
)
```

#### 哈希字符串存储格式：
`$argon2id$v=19$m=65536,t=3,p=4$<salt_base64>$<digest_base64>`

#### 密码验证与平滑重哈希（Rehash on Cost Upgrade）：
```python
def verify_and_update_password(plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
    """验证密码，并在哈希参数升级时自动返回新哈希以便更新落库。"""
    try:
        argon2_hasher.verify(hashed_password, plain_password)
        # 检查当前存储的哈希是否需要根据新参数重新计算
        if argon2_hasher.check_needs_rehash(hashed_password):
            new_hash = argon2_hasher.hash(plain_password)
            return True, new_hash
        return True, None
    except Exception:
        return False, None
```

### 2.2 防暴力破解与账户锁定机制

1. **计数与窗口**：5 分钟内连续 5 次密码验证失败，账户状态进入 `locked`，锁定时间 `locked_until = NOW() + INTERVAL '15 minutes'`。
2. **渐进式时延**：每次连续失败增加指数级响应延时（500ms -> 1000ms -> 2000ms），削弱在线爆破效率。
3. **审计记录**：每次认证失败记录 `auth_audit_events`，记录来源 IP 与 `trace_id`。

---

## 3. Session 与 CSRF 存储模型 (Redis + DB 双层架构)

### 3.1 Redis 会话存储结构

Redis 作为主运行时会话存储，提供微秒级会话查找与续期能力。

```
Key 格式: session:{session_id}
类型: HASH
TTL: 86400 (24 小时，滑动续期)

字段结构：
├── user_id: "usr_9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
├── username: "trader_jack"
├── roles: "[\"trader\", \"researcher\"]"
├── permissions: "[\"stocks:read\", \"watchlists:write\", \"tags:write\"]"
├── csrf_token: "c_9f8e7d6c5b4a3210e4f5..."
├── ip_address: "192.168.1.100"
├── user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)..."
├── created_at: "1773129600"
└── last_seen_at: "1773130200"
```

### 3.2 用户会话索引（用于单点登录与多端踢出）

```
Key 格式: user_sessions:{user_id}
类型: SET
成员: "sess_8f7e6d5c4b3a210f", "sess_99a8b7c6d5e4f3a2"
```

- 当用户主动点击“退出所有其他设备”时，查询 `user_sessions:{user_id}` 集合，逐一从 Redis 删除会话，并在 `auth_sessions` 表中将对应记录标记为 `is_revoked = TRUE`。

### 3.3 CSRF 令牌验证逻辑

1. 登录成功时，Auth Service 生成高熵随机 CSRF 令牌（32 bytes secure random url-safe string）。
2. 将 CSRF 令牌写入 Redis Session 属性 `csrf_token`。
3. Gateway 接收到客户端带有 Cookie 的非幂等请求时：
   - 从 Redis 取出 Session 绑定的 `csrf_token`；
   - 提取请求头 `X-CSRF-Token`；
   - 使用常数时间字符串比对（`hmac.compare_digest`）两者；
   - 比对一致则放行，否则拒绝并返回 `403 AUTH_CSRF_INVALID`。

---

## 4. Refresh Token 家族轮换与重放检测 (RTR & Replay Attack Defense)

针对需要长周期免登录或移动端 API 的场景，采用 **Refresh Token Rotation (RTR) + Token Family** 算法：

```
[客户端]                                [Auth Service]
   │                                          │
   │ 1. POST /auth/refresh (Token A, Gen 1)   │
   │ ───────────────────────────────────────> │ 校验 Token A (属于 Family F1, Gen 1, 未使用)
   │                                          │ 标记 Token A.is_used = TRUE
   │                                          │ 生成 Token B (Family F1, Gen 2)
   │ 2. 返回 Token B                          │
   │ <─────────────────────────────────────── │
   │                                          │
   │ ... 攻击者窃取旧 Token A 并尝试刷新 ...  │
   │ 3. POST /auth/refresh (Token A, Gen 1)   │
   │ ───────────────────────────────────────> │ 检测到 Token A.is_used == TRUE (发生重放攻击!)
   │                                          │ 【紧急处置】：
   │                                          │ 1. 立即吊销 Family F1 下的所有 Token!
   │                                          │ 2. 销毁关联用户的全部活跃 Session
   │ 4. 返回 401 AUTH_REPLAY_DETECTED         │ 3. 记录最高等级安全审计告警
   │ <─────────────────────────────────────── │
```

---

## 5. 业务数据模型调整设计 (多用户与数据归属隔离)

在引入认证体系后，原单用户/全局共享的业务模型需要进行归属隔离与权限调整。

### 5.1 用户自定义申万标签表改造 (`stock_custom_sw_tags`)

**现状**：当前 `stock_custom_sw_tags` 仅有 `(symbol, industry_code)` 唯一约束，所有用户共享/互相覆盖标签。

**改造方案**：增加 `user_id` 列，实现标签按用户隔离。

```sql
-- 迁移脚本：stock_custom_sw_tags 增加 user_id 隔离
ALTER TABLE stock_custom_sw_tags ADD COLUMN user_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000';

-- 移除旧全局唯一约束
ALTER TABLE stock_custom_sw_tags DROP CONSTRAINT IF EXISTS uq_custom_sw_tag;

-- 增加基于 (user_id, symbol, industry_code) 的租户级联合唯一约束
ALTER TABLE stock_custom_sw_tags ADD CONSTRAINT uq_user_custom_sw_tag UNIQUE (user_id, symbol, industry_code);

-- 优化查询索引
CREATE INDEX idx_user_custom_sw_tags_lookup ON stock_custom_sw_tags(user_id, symbol);
CREATE INDEX idx_user_custom_sw_tags_industry ON stock_custom_sw_tags(user_id, industry_code);

COMMENT ON COLUMN stock_custom_sw_tags.user_id IS '创建该自定义标签的用户ID (UUID)';
```

**SQLAlchemy 2.0 ORM 变更**：

```python
class StockCustomSwTag(Base):
    """User-assigned SW L2/L3 industry tags for a stock (scoped per user)."""

    __tablename__ = "stock_custom_sw_tags"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "industry_code", name="uq_user_custom_sw_tag"),
        Index("idx_user_custom_sw_tag_lookup", "user_id", "symbol"),
        Index("idx_user_custom_sw_tag_industry", "user_id", "industry_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

### 5.2 自选股服务端化 (`user_watchlists` 与 `user_watchlist_items`)

**现状**：当前自选股仅保存在前端 `localStorage` (`stock-bot-watchlist`) 中，换设备或清理缓存后丢失，无法云端同步与多端共享。

**改造方案**：新增服务端自选分组与自选股清单两张表。

```sql
-- 1. 用户自选股分组表
CREATE TABLE user_watchlists (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    name            VARCHAR(64) NOT NULL DEFAULT '默认分组',
    sort_order      INT NOT NULL DEFAULT 0,
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_watchlist_name UNIQUE (user_id, name)
);

CREATE INDEX idx_user_watchlists_user ON user_watchlists(user_id, sort_order);

-- 2. 用户自选股明细表
CREATE TABLE user_watchlist_items (
    id              BIGSERIAL PRIMARY KEY,
    watchlist_id    UUID NOT NULL REFERENCES user_watchlists(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL, -- 冗余 user_id 加速单表复合索引过滤
    symbol          VARCHAR(16) NOT NULL,
    exchange        VARCHAR(32) NOT NULL,
    notes           VARCHAR(255),
    sort_order      INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_watchlist_item UNIQUE (watchlist_id, symbol)
);

CREATE INDEX idx_user_watchlist_items_query ON user_watchlist_items(user_id, watchlist_id, sort_order);
CREATE INDEX idx_user_watchlist_items_symbol ON user_watchlist_items(user_id, symbol);
```

### 5.3 异步任务表改造 (`tasks` 增加 `requested_by`)

**现状**：`tasks` 表没有记录发起人，无法审计是谁触发了全量回补或聚类任务。

**改造方案**：新增 `requested_by` 字段，并区分系统定时任务（`requested_by IS NULL`）与用户触发。

```sql
-- 迁移脚本：tasks 增加 requested_by
ALTER TABLE tasks ADD COLUMN requested_by UUID;

CREATE INDEX idx_tasks_requested_by ON tasks(requested_by, created_at DESC);

COMMENT ON COLUMN tasks.requested_by IS '发起该任务的用户 UUID，定时任务为 NULL';
```

**SQLAlchemy 2.0 ORM 变更**：

```python
class Task(Base):
    """Tracks the state of an async background job with initiator auditing."""

    __tablename__ = "tasks"
    __table_args__ = (
        TASK_STATUS_CHECK,
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_type", "type"),
        Index("idx_tasks_created", "created_at"),
        Index("idx_tasks_requested_by", "requested_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(default=0)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

### 5.4 Redis 业务缓存隔离策略

为防止跨用户数据泄露与脏读，Redis 业务缓存键严格执行作用域前缀命名空间：

| 数据类型 | 作用域 | 缓存 Key 格式 | 示例 | TTL |
| :--- | :--- | :--- | :--- | :--- |
| **公开行情/指标** | 全局共享 | `cache:global:{domain}:{metric}:{id}` | `cache:global:quote:latest:000001.SZ` | 30s |
| **申万行业树** | 全局共享 | `cache:global:sw:tree` | `cache:global:sw:tree` | 3600s |
| **投研工作台看板** | 全局共享 | `cache:global:research:dashboard:{key}` | `cache:global:research:dashboard:pig` | 60s |
| **用户自选列表** | 用户私有 | `cache:user:{user_id}:watchlists` | `cache:user:usr_9b1deb4d...:watchlists` | 300s |
| **用户自定义标签** | 用户私有 | `cache:user:{user_id}:tags:{symbol}` | `cache:user:usr_9b1deb4d...:tags:000001` | 600s |
| **用户权限列表** | 用户私有 | `cache:user:{user_id}:permissions` | `cache:user:usr_9b1deb4d...:permissions` | 1800s |

**缓存失效契约**：
- 用户更新自选股时，精准清除 `cache:user:{user_id}:watchlists`；
- 用户打标签时，不仅清除 `cache:user:{user_id}:tags:{symbol}`，还需失效该用户维度的聚合行业统计。

---

## 6. 存量标签归属与认领策略（迁移 `5a1b2c3d4e5f`）

### 6.1 迁移行为回顾

`backend/app/migrations/versions/5a1b2c3d4e5f_add_user_ownership_and_watchlists.py`
为 `stock_user_tags` 增加 `user_id UUID NOT NULL` 列。由于存量行没有归属用户，
迁移采用**固定服务端默认值回填**（非 `gen_random_uuid()` 随机生成）：

```sql
ALTER TABLE stock_user_tags
    ADD COLUMN user_id UUID NOT NULL
    DEFAULT '00000000-0000-0000-0000-000000000000'::uuid;  -- 全零 UUID（nil UUID）
ALTER TABLE stock_user_tags ALTER COLUMN user_id DROP DEFAULT;  -- 回填后移除默认值
```

即：**所有迁移前已存在的标签，其 `user_id` 一律为全零 UUID
`00000000-0000-0000-0000-000000000000`**（同一固定值，非每行随机）。

### 6.2 全零 UUID 的语义：幽灵/系统迁移用户

- 全零 UUID 属于"幽灵/系统迁移用户"——它不对应 `auth_users` 表中任何真实账号，
  仅作为存量数据的占位归属，避免 NOT NULL 约束下的回填失败。
- 对所有真实用户**不可见**：查询路径均以当前登录用户的 `user_id` 过滤
  （路由层注入 Principal + 缓存键 `user:{user_id}:*` 命名空间），
  没有任何真实会话会以全零 UUID 查询，因此存量标签不会泄漏给任何用户，
  也不会出现在任何用户界面上。
- 请勿在 `auth_users` 中创建 id 为全零 UUID 的账号来"接管"这批数据。

### 6.3 管理员认领 SQL 模板

若业务上需要把某批存量标签划归某个真实用户（如管理员代运营账号），在**业务库**
（stock_bot 主库，非 auth 库）执行：

```sql
-- :admin_uuid  = 目标用户的 auth_users.id（UUID）
-- :legacy_uuid = 全零 UUID 00000000-0000-0000-0000-000000000000
-- 可选追加 AND symbol = ... / tag_name = ... 限定认领范围
UPDATE stock_user_tags
SET user_id = :admin_uuid
WHERE user_id = :legacy_uuid;
```

**注意事项**：

1. **唯一约束冲突**：表上有 `(user_id, symbol, tag_name)` 唯一约束
   （`uq_stock_user_tag`）。若目标用户已存在相同 `(symbol, tag_name)` 的标签，
   UPDATE 会整批失败。先排查冲突：
   ```sql
   SELECT t.symbol, t.tag_name
   FROM stock_user_tags t
   WHERE t.user_id = :legacy_uuid
     AND EXISTS (
       SELECT 1 FROM stock_user_tags e
       WHERE e.user_id = :admin_uuid
         AND e.symbol = t.symbol AND e.tag_name = t.tag_name
     );
   ```
   冲突行需人工决定去重方向（删除存量或删除目标用户同名行）后再认领。
2. **事务执行**：认领语句包在事务中执行，确认影响行数符合预期后再提交。
3. **缓存失效**：认领后目标用户的 Redis 业务缓存
   （`cache:user:{admin_uuid}:tags:*` 及其用户维度聚合统计）需要清除或等待自然过期，
   否则用户可能短暂看不到新认领的标签。
4. **审计留痕**：认领属于数据归属变更，建议记录操作人、时间与影响行数，
   便于追溯。
