"""Initial auth schema and preset roles/permissions

Revision ID: a001_initial_auth
Revises:
Create Date: 2026-09-09 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a001_initial_auth"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. auth_users
    op.create_table(
        "auth_users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'locked', 'pending_verification')",
            name="chk_auth_user_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_auth_users_username"),
        sa.UniqueConstraint("email", name="uq_auth_users_email"),
    )
    op.create_index("idx_auth_users_status", "auth_users", ["status"])
    op.create_index(
        "idx_auth_users_created_at",
        "auth_users",
        [sa.text("created_at DESC")],
    )

    # 2. auth_credentials
    op.create_table(
        "auth_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_type", sa.String(length=32), server_default="password", nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("salt", sa.String(length=64), nullable=True),
        sa.Column(
            "password_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_users.id"],
            name="fk_auth_credentials_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "auth_type", name="uq_auth_credentials_user_type"),
    )
    op.create_index("idx_auth_credentials_user_id", "auth_credentials", ["user_id"])

    # 3. auth_roles
    op.create_table(
        "auth_roles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 4. auth_permissions
    op.create_table(
        "auth_permissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. auth_role_permissions
    op.create_table(
        "auth_role_permissions",
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.Column("permission_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["auth_permissions.id"],
            name="fk_auth_role_permissions_perm",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["auth_roles.id"],
            name="fk_auth_role_permissions_role",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )

    # 6. auth_user_roles
    op.create_table(
        "auth_user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["auth_users.id"],
            name="fk_auth_user_roles_assigner",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["auth_roles.id"],
            name="fk_auth_user_roles_role",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_users.id"],
            name="fk_auth_user_roles_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )
    op.create_index("idx_auth_user_roles_user", "auth_user_roles", ["user_id"])

    # 7. auth_sessions
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("device_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_users.id"],
            name="fk_auth_sessions_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_auth_sessions_user_id",
        "auth_sessions",
        ["user_id", "is_revoked"],
    )
    op.create_index("idx_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    # 8. auth_refresh_token_families
    op.create_table(
        "auth_refresh_token_families",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_users.id"],
            name="fk_auth_refresh_token_families_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_auth_token_hash"),
    )
    op.create_index("idx_auth_rt_family", "auth_refresh_token_families", ["family_id"])
    op.create_index("idx_auth_rt_user", "auth_refresh_token_families", ["user_id"])

    # 9. auth_audit_events
    op.create_table(
        "auth_audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_users.id"],
            name="fk_auth_audit_events_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_auth_audit_user_id",
        "auth_audit_events",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_auth_audit_event_type",
        "auth_audit_events",
        ["event_type", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_auth_audit_created_at",
        "auth_audit_events",
        [sa.text("created_at DESC")],
    )

    # Preset roles
    roles_table = sa.table(
        "auth_roles",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_system", sa.Boolean),
    )
    op.bulk_insert(
        roles_table,
        [
            {
                "id": "admin",
                "name": "系统管理员",
                "description": "拥有系统全量管理权限与任务调度权限",
                "is_system": True,
            },
            {
                "id": "researcher",
                "name": "专业投研员",
                "description": "具备数据查看、自定义标签、投研工作台配置、个人自选权限",
                "is_system": True,
            },
            {
                "id": "analyst",
                "name": "分析师",
                "description": "具备投研工作台与数据分析权限 (researcher 别名)",
                "is_system": True,
            },
            {
                "id": "trader",
                "name": "普通交易员",
                "description": "具备股票数据与自选股基础功能",
                "is_system": True,
            },
            {
                "id": "operator",
                "name": "业务操作员",
                "description": "具备基础数据与自选股操作权限 (trader 别名)",
                "is_system": True,
            },
            {
                "id": "viewer",
                "name": "只读访客",
                "description": "仅具备公开行情数据查看权限",
                "is_system": True,
            },
        ],
    )

    # Preset permissions
    permissions_table = sa.table(
        "auth_permissions",
        sa.column("id", sa.String),
        sa.column("module", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
    )
    all_perms = [
        {
            "id": "stocks:read",
            "module": "stocks",
            "name": "查看股票行情",
            "description": "允许查询个股详情、分时与日K行情",
        },
        {
            "id": "market:read",
            "module": "market",
            "name": "查看市场与行业",
            "description": "允许查询申万行业与市场概览",
        },
        {
            "id": "research:read",
            "module": "research",
            "name": "查看投研工作台",
            "description": "允许查看生猪等行业投研工作台",
        },
        {
            "id": "watchlists:write",
            "module": "watchlists",
            "name": "管理个人自选股",
            "description": "允许新增、编辑、删除个人自选股",
        },
        {
            "id": "tags:write",
            "module": "tags",
            "name": "编辑用户自定义标签",
            "description": "允许为股票打自定义申万分类标签",
        },
        {
            "id": "tasks:read",
            "module": "tasks",
            "name": "查看后台任务",
            "description": "允许查看数据采集与回填任务进度",
        },
        {
            "id": "tasks:trigger",
            "module": "tasks",
            "name": "触发后台任务",
            "description": "允许手动触发采集、回填与聚类计算",
        },
        {
            "id": "users:manage",
            "module": "admin",
            "name": "管理用户账户",
            "description": "允许创建用户、封禁账户与重置密码",
        },
        {
            "id": "roles:manage",
            "module": "admin",
            "name": "分配角色权限",
            "description": "允许变更用户角色分配",
        },
        {
            "id": "audit:read",
            "module": "admin",
            "name": "查看审计日志",
            "description": "允许查询安全审计日志",
        },
    ]
    op.bulk_insert(permissions_table, all_perms)

    # Preset role_permissions
    role_perms_table = sa.table(
        "auth_role_permissions",
        sa.column("role_id", sa.String),
        sa.column("permission_id", sa.String),
    )
    role_perm_mappings = []
    # admin gets all permissions
    for p in all_perms:
        role_perm_mappings.append({"role_id": "admin", "permission_id": p["id"]})

    # researcher / analyst
    for p_id in [
        "stocks:read",
        "market:read",
        "research:read",
        "watchlists:write",
        "tags:write",
        "tasks:read",
    ]:
        role_perm_mappings.append({"role_id": "researcher", "permission_id": p_id})
        role_perm_mappings.append({"role_id": "analyst", "permission_id": p_id})

    # trader / operator
    for p_id in ["stocks:read", "market:read", "watchlists:write", "tasks:read"]:
        role_perm_mappings.append({"role_id": "trader", "permission_id": p_id})
        role_perm_mappings.append({"role_id": "operator", "permission_id": p_id})

    # viewer
    for p_id in ["stocks:read", "market:read"]:
        role_perm_mappings.append({"role_id": "viewer", "permission_id": p_id})

    op.bulk_insert(role_perms_table, role_perm_mappings)


def downgrade() -> None:
    op.drop_table("auth_audit_events")
    op.drop_table("auth_refresh_token_families")
    op.drop_table("auth_sessions")
    op.drop_table("auth_user_roles")
    op.drop_table("auth_role_permissions")
    op.drop_table("auth_permissions")
    op.drop_table("auth_roles")
    op.drop_table("auth_credentials")
    op.drop_table("auth_users")
