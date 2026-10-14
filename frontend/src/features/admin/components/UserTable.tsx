import { useMemo, useState } from "react";
import { Button, Input, Space, Table, Tabs, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { listUsers, type AdminUser } from "@/shared/api/admin";
import { ProvisionUserModal } from "./ProvisionUserModal";
import { UserDetailDrawer } from "./UserDetailDrawer";
import { RoleTags, StatusTag } from "./StatusTag";

const PAGE_SIZE = 20;

/** 管理台主页：状态筛选（计数来自服务端 total）+ 搜索 + 服务端分页。 */
export function UserTable() {
  const [status, setStatus] = useState<string>("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [provisionOpen, setProvisionOpen] = useState(false);
  const [detail, setDetail] = useState<AdminUser | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "users", { status, q, page }],
    queryFn: () =>
      listUsers({
        status: status || undefined,
        q: q || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
  });

  // 计数：全部 + 正常（各自一次 ?page_size=1 读 total，不设独立 summary 端点）
  const { data: all } = useQuery({
    queryKey: ["admin", "users", { count: "all" }],
    queryFn: () => listUsers({ page: 1, page_size: 1 }),
    staleTime: 30 * 1000,
  });
  const { data: activeCount } = useQuery({
    queryKey: ["admin", "users", { count: "active" }],
    queryFn: () => listUsers({ status: "active", page: 1, page_size: 1 }),
    staleTime: 30 * 1000,
  });

  const tabItems = useMemo(
    () => [
      { key: "", label: `全部 ${all?.total ?? "—"}` },
      { key: "active", label: `正常 ${activeCount?.total ?? "—"}` },
    ],
    [all, activeCount]
  );

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <Typography.Title level={4} style={{ margin: 0 }}>
          用户管理
        </Typography.Title>
        <Button type="primary" onClick={() => setProvisionOpen(true)}>
          开户
        </Button>
      </div>

      <Space style={{ marginBottom: 12, width: "100%", justifyContent: "space-between" }}>
        <Tabs
          activeKey={status}
          onChange={(k) => {
            setStatus(k);
            setPage(1);
          }}
          items={tabItems}
          style={{ marginBottom: 0 }}
        />
        <Input.Search
          placeholder="搜索用户名 / 邮箱"
          allowClear
          style={{ width: 240 }}
          onSearch={(v) => {
            setQ(v);
            setPage(1);
          }}
        />
      </Space>

      <Table<AdminUser>
        rowKey="id"
        loading={isLoading}
        dataSource={data?.items}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
        }}
        onRow={(record) => ({ onClick: () => setDetail(record), style: { cursor: "pointer" } })}
        columns={[
          { title: "用户名", dataIndex: "username", width: 160 },
          { title: "显示名", dataIndex: "display_name", width: 140, render: (v) => v || "—" },
          {
            title: "状态",
            dataIndex: "status",
            width: 140,
            render: (_, u) => <StatusTag user={u} />,
          },
          {
            title: "角色",
            dataIndex: "roles",
            render: (roles: string[]) => <RoleTags roles={roles} />,
          },
          {
            title: "最近登录",
            dataIndex: "last_login_at",
            width: 170,
            render: (v) => (v ? new Date(v).toLocaleString() : "从未登录"),
          },
          { title: "", key: "op", width: 60, render: () => <a>详情</a> },
        ]}
      />

      <ProvisionUserModal open={provisionOpen} onClose={() => setProvisionOpen(false)} />
      <UserDetailDrawer user={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
