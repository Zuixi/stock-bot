import { Tag, Tooltip } from "antd";
import type { AdminUser } from "@/shared/api/admin";

/**
 * Account status rendered in the platform's mother tongue: market semantics.
 * active=持仓绿 suspended=停牌红 locked=临时锁定灰 + 待改密蓝点.
 * (UX 设计：状态即行情 —— 唯一的大胆处，其余克制。)
 */
const STATUS_META: Record<string, { color: string; label: string }> = {
  active: { color: "var(--down)", label: "正常" },
  suspended: { color: "var(--up)", label: "已停用" },
  locked: { color: "var(--text-secondary)", label: "已锁定" },
  pending_verification: { color: "var(--text-secondary)", label: "待验证" },
};

export function StatusTag({ user }: { user: AdminUser }) {
  const meta = STATUS_META[user.status] ?? {
    color: "var(--text-secondary)",
    label: user.status,
  };
  return (
    <Tooltip
      title={user.status_reason ? `停用原因：${user.status_reason}` : undefined}
      open={user.status_reason ? undefined : false}
    >
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <span
          aria-hidden
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: meta.color,
            display: "inline-block",
            flex: "0 0 auto",
          }}
        />
        {meta.label}
        {user.must_change_password && (
          <Tag style={{ marginInlineEnd: 0 }} color="blue">
            待改密
          </Tag>
        )}
      </span>
    </Tooltip>
  );
}

const ROLE_COLORS: Record<string, string> = {
  admin: "gold",
  researcher: "geekblue",
  analyst: "blue",
  trader: "green",
  operator: "cyan",
  viewer: "default",
};

export function RoleTags({ roles }: { roles: string[] }) {
  if (!roles.length) return <span style={{ color: "var(--text-secondary)" }}>—</span>;
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      {roles.map((r) => (
        <Tag key={r} color={ROLE_COLORS[r] ?? "default"} style={{ marginInlineEnd: 0 }}>
          {r}
        </Tag>
      ))}
    </span>
  );
}
