const BASE_URL = import.meta.env.VITE_API_BASE ?? "";

export interface ErrorResponse {
  code: string;
  message: string;
  details?: unknown;
  trace_id: string;
}

export class ApiError extends Error {
  public code: string;
  public status: number;
  public details?: unknown;
  public traceId?: string;

  constructor(status: number, message: string, code?: string, details?: unknown, traceId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code || `HTTP_${status}`;
    this.details = details;
    this.traceId = traceId;
  }
}

export interface RequestOptions {
  params?: Record<string, string | number | boolean | undefined | null>;
  skipAuth?: boolean;
  skipCsrf?: boolean;
}

let csrfPromise: Promise<string> | null = null;

export function getCsrfTokenFromCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)stockbot_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

export async function fetchCsrfToken(): Promise<string> {
  const existing = getCsrfTokenFromCookie();
  if (existing) {
    return existing;
  }

  if (csrfPromise) {
    return csrfPromise;
  }

  csrfPromise = (async () => {
    try {
      const url = new URL(`${BASE_URL}/auth/csrf`, window.location.origin);
      const res = await fetch(url.toString(), {
        method: "GET",
        credentials: "include",
      });
      if (!res.ok) {
        throw new Error("Failed to fetch CSRF token");
      }
      const data = (await res.json()) as { csrf_token: string };
      return data.csrf_token;
    } finally {
      csrfPromise = null;
    }
  })();

  return csrfPromise;
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {}
): Promise<T> {
  const url = new URL(`${BASE_URL}${path}`, window.location.origin);
  if (options.params) {
    Object.entries(options.params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) {
        url.searchParams.set(k, String(v));
      }
    });
  }

  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);

  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  if (init.body && typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  // Auto CSRF for mutating methods
  if (["POST", "PUT", "DELETE", "PATCH"].includes(method) && !options.skipCsrf) {
    let token = getCsrfTokenFromCookie();
    if (!token) {
      try {
        token = await fetchCsrfToken();
      } catch {
        // Fallback: continue without header or gateway will intercept
      }
    }
    if (token && !headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", token);
    }
  }

  const response = await fetch(url.toString(), {
    ...init,
    method,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    let errorData: Partial<ErrorResponse> = {};
    try {
      errorData = await response.json();
    } catch {
      // Body is not JSON
    }

    const traceId =
      errorData.trace_id ||
      response.headers.get("X-Request-Id") ||
      response.headers.get("X-Trace-Id") ||
      undefined;

    const errorCode = errorData.code || `HTTP_${response.status}`;
    const errorMessage = errorData.message || response.statusText || "请求失败";

    if (response.status === 401 && !options.skipAuth) {
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("auth:unauthorized", {
            detail: { status: 401, code: errorCode, message: errorMessage },
          })
        );
      }
    }

    throw new ApiError(response.status, errorMessage, errorCode, errorData.details, traceId);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined | null>,
  options: RequestOptions = {}
): Promise<T> {
  return request<T>(path, { method: "GET" }, { ...options, params });
}

export function apiPost<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<T> {
  return request<T>(
    path,
    {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    options
  );
}

export function apiPut<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<T> {
  return request<T>(
    path,
    {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    options
  );
}

export function apiPatch<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<T> {
  return request<T>(
    path,
    {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    },
    options
  );
}

export function apiDelete<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  return request<T>(path, { method: "DELETE" }, options);
}
