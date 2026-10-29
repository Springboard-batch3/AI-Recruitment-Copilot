/**
 * Centralized API Client
 * Automatically includes credentials (HttpOnly cookie) and injects in-memory CSRF token on mutations.
 */

let currentCsrfToken: string | null = null;
let onUnauthorizedCallback: (() => void) | null = null;

export function setApiCsrfToken(token: string | null): void {
  currentCsrfToken = token;
}

export function getApiCsrfToken(): string | null {
  return currentCsrfToken;
}

export function setOnUnauthorizedCallback(callback: (() => void) | null): void {
  onUnauthorizedCallback = callback;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, message: string, code: string = "UNKNOWN_ERROR") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export async function apiFetch<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const isMutation = ["POST", "PUT", "PATCH", "DELETE"].includes(method);

  const headers = new Headers(options.headers || {});

  // Add CSRF token for state-changing requests if present
  if (isMutation && currentCsrfToken && !headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", currentCsrfToken);
  }

  // Set default JSON Content-Type if body is string and not FormData
  if (options.body && typeof options.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(endpoint, {
    ...options,
    headers,
    credentials: "include", // ALWAYS include HttpOnly session cookie
  });

  if (response.status === 401) {
    // Only invoke global session-expired callback if this wasn't an expected unauthenticated probe (like /auth/me during restore or /auth/login)
    const isAuthProbe = endpoint.includes("/auth/me") || endpoint.includes("/auth/login");
    if (!isAuthProbe && onUnauthorizedCallback) {
      onUnauthorizedCallback();
    }
  }

  if (!response.ok) {
    let errorDetail: ApiErrorDetail = {
      code: "API_ERROR",
      message: `Request failed with status ${response.status}`,
    };

    try {
      const data = await response.json();
      if (data?.detail) {
        if (typeof data.detail === "string") {
          errorDetail.message = data.detail;
        } else if (Array.isArray(data.detail) && data.detail.length > 0) {
          // FastAPI / Pydantic validation error array: [{loc, msg, type}]
          const messages = data.detail.map((d: any) => {
            const field = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
            return field ? `${field}: ${d.msg}` : d.msg;
          });
          errorDetail.message = messages.join("; ");
          errorDetail.code = "VALIDATION_ERROR";
        } else if (typeof data.detail === "object") {
          errorDetail.code = data.detail.code || errorDetail.code;
          errorDetail.message = data.detail.message || errorDetail.message;
        }
      }
    } catch {
      // Non-JSON error payload
    }

    throw new ApiError(response.status, errorDetail.message, errorDetail.code);
  }

  if (response.status === 204) {
    return null as unknown as T;
  }

  return response.json();
}

export interface DashboardSessionResponse {
  ticket: string;
  launch_url: string;
  expires_in_seconds: number;
}

export async function createDashboardSession(): Promise<DashboardSessionResponse> {
  return apiFetch<DashboardSessionResponse>("/api/v1/dashboard/session", {
    method: "POST",
  });
}

