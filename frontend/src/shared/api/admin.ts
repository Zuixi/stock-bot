import { apiGet, apiPost, apiPut, type RequestOptions } from "./client";

/** Admin console API client (ADR 0010, /auth/admin/*). */

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  display_name?: string | null;
  status: "active" | "suspended" | "locked" | string;
  status_reason?: string | null;
  is_superuser: boolean;
  must_change_password: boolean;
  roles: string[];
  permissions: string[];
  last_login_at?: string | null;
  created_at: string;
}

export interface PagedUsers {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
}

export interface RoleInfo {
  id: string;
  name: string;
  description?: string | null;
  is_system: boolean;
  permissions: string[];
}

export interface ProvisionResult {
  user: AdminUser;
  initial_password: string;
  expires_at: string;
}

export function listUsers(
  params: { status?: string; q?: string; page?: number; page_size?: number },
  options?: RequestOptions
): Promise<PagedUsers> {
  return apiGet<PagedUsers>("/auth/admin/users", params, options);
}

export function provisionUser(
  payload: { username: string; email: string; display_name?: string; roles: string[] },
  options?: RequestOptions
): Promise<ProvisionResult> {
  return apiPost<ProvisionResult>("/auth/admin/users", payload, options);
}

export function suspendUser(
  id: string,
  reason?: string,
  options?: RequestOptions
): Promise<AdminUser> {
  return apiPost<AdminUser>(`/auth/admin/users/${id}/suspend`, { reason }, options);
}

export function reactivateUser(id: string, options?: RequestOptions): Promise<AdminUser> {
  return apiPost<AdminUser>(`/auth/admin/users/${id}/reactivate`, {}, options);
}

export function replaceRoles(
  id: string,
  roles: string[],
  options?: RequestOptions
): Promise<AdminUser> {
  return apiPut<AdminUser>(`/auth/admin/users/${id}/roles`, { roles }, options);
}

export function resetPassword(
  id: string,
  options?: RequestOptions
): Promise<ProvisionResult> {
  return apiPost<ProvisionResult>(`/auth/admin/users/${id}/reset-password`, {}, options);
}

export function listRoles(options?: RequestOptions): Promise<RoleInfo[]> {
  return apiGet<RoleInfo[]>("/auth/admin/roles", undefined, options);
}
