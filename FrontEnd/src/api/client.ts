import { storage } from "../utils/storage";

export class NetError extends Error {
  constructor(message = "Cannot reach the server") {
    super(message);
    this.name = "NetError";
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const API_BASE_STORAGE_KEY = "apiBaseUrl";
const TOKEN_STORAGE_KEY = "token";

export function getDefaultApiBaseUrl(): string {
  const envUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
  if (envUrl) return envUrl.replace(/\/+$/, "");
  return "/api";
}

export function getApiBaseUrl(): string {
  const saved = storage.get<string>(API_BASE_STORAGE_KEY, "").trim();
  if (saved) return saved.replace(/\/+$/, "");
  return getDefaultApiBaseUrl();
}

export function setApiBaseUrl(url: string): void {
  const cleaned = url.trim().replace(/\/+$/, "");
  if (!cleaned) {
    storage.del(API_BASE_STORAGE_KEY);
  } else {
    storage.set(API_BASE_STORAGE_KEY, cleaned);
  }
}

export function getStoredToken(): string | null {
  return storage.get<string | null>(TOKEN_STORAGE_KEY, null);
}

export function setStoredToken(token: string | null): void {
  if (token) {
    storage.set(TOKEN_STORAGE_KEY, token);
  } else {
    storage.del(TOKEN_STORAGE_KEY);
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function registerUnauthorizedHandler(fn: () => void): void {
  unauthorizedHandler = fn;
}

export async function apiRequest<T>(
  method: "GET" | "POST" | "PUT",
  path: string,
  body?: unknown,
  options?: { baseUrlOverride?: string; skipAuth?: boolean }
): Promise<T> {
  const baseUrl = (options?.baseUrlOverride ?? getApiBaseUrl()).replace(/\/+$/, "");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getStoredToken();
  if (token && !options?.skipAuth && path !== "/login" && path !== "/health") {
    headers.Authorization = `Bearer ${token}`;
  }

  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), 8000);
  let resp: Response;
  try {
    resp = await fetch(`${baseUrl}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      cache: "no-store",
    });
  } catch {
    throw new NetError();
  } finally {
    window.clearTimeout(timer);
  }

  const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>;

  if (resp.status === 401 && path !== "/login") {
    unauthorizedHandler?.();
    throw new ApiError(401, String(data.error ?? "not logged in"));
  }

  if (!resp.ok) {
    throw new ApiError(resp.status, String(data.error ?? `HTTP ${resp.status}`));
  }

  return data as T;
}
