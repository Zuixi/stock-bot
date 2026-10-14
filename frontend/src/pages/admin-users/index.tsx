import { useEffect } from "react";
import { UserTable } from "@/features/admin";

export default function AdminUsersPage() {
  useEffect(() => {
    document.title = "用户管理 · Stock Bot";
  }, []);
  return <UserTable />;
}
