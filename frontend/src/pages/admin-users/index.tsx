import { useEffect } from "react";
import { UserTable } from "@/features/admin";

export default function AdminUsersPage() {
  useEffect(() => {
    document.title = "用户管理 · StockBot";
  }, []);
  return <UserTable />;
}
