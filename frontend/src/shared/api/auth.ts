import { apiGet, apiPost, fetchCsrfToken, type RequestOptions } from "./client";

export interface UserProfile {
  id: string;
  username: string;
  email: string;
  display_name?: string | null;
  status: string;
  is_superuser: boolean;
  roles: string[];
  permissions: string[];
  last_login_at?: string | null;
  created_at: string;
}

export interface UserLoginPayload {
  username_or_email: string;
  password: string;
}

export interface UserRegisterPayload {
  username: string;
  email: string;
  password: string;
  display_name?: string;
}

export interface AuthResponseData {
  user: UserProfile;
  expires_in: number;
}

export interface CsrfResponseData {
  csrf_token: string;
}

export interface SessionInfo {
  id: string;
  user_id: string;
  ip_address?: string | null;
  user_agent?: string | null;
  is_current: boolean;
  expires_at: string;
  last_active_at: string;
  created_at: string;
}

/**
 * Login user with credentials and obtain session cookies.
 */
export function login(
  payload: UserLoginPayload,
  options?: RequestOptions
): Promise<AuthResponseData> {
  return apiPost<AuthResponseData>("/auth/login", payload, {
    skipAuth: true,
    ...options,
  });
}

/**
 * Register a new user account.
 */
export function register(
  payload: UserRegisterPayload,
  options?: RequestOptions
): Promise<UserProfile> {
  return apiPost<UserProfile>("/auth/register", payload, {
    skipAuth: true,
    ...options,
  });
}

/**
 * Logout current session and clear server/client cookies.
 */
export function logout(options?: RequestOptions): Promise<{ message: string }> {
  return apiPost<{ message: string }>("/auth/logout", {}, options);
}

/**
 * Get current session profile and permissions.
 */
export function getSession(options?: RequestOptions): Promise<UserProfile> {
  return apiGet<UserProfile>("/auth/session", undefined, options);
}

/**
 * Alias for getSession using /auth/me endpoint.
 */
export function getMe(options?: RequestOptions): Promise<UserProfile> {
  return apiGet<UserProfile>("/auth/me", undefined, options);
}

/**
 * Get or refresh CSRF token.
 */
export function getCsrf(options?: RequestOptions): Promise<CsrfResponseData> {
  return apiGet<CsrfResponseData>("/auth/csrf", undefined, {
    skipAuth: true,
    skipCsrf: true,
    ...options,
  });
}

/**
 * List active user sessions.
 */
export function listSessions(options?: RequestOptions): Promise<SessionInfo[]> {
  return apiGet<SessionInfo[]>("/auth/sessions", undefined, options);
}

export { fetchCsrfToken };
