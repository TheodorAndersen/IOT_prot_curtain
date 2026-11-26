import type {
  AutoRequest,
  DebugResponse,
  HealthResponse,
  LoginRequest,
  LoginResponse,
  ModeRequest,
  OkResponse,
  PositionRequest,
  SettingsRequest,
  StatusResponse,
} from "../types/api";
import { apiRequest } from "./client";

export const api = {
  getHealth: (baseUrlOverride?: string) =>
    apiRequest<HealthResponse>("GET", "/health", undefined, {
      baseUrlOverride,
      skipAuth: true,
    }),

  login: (req: LoginRequest) =>
    apiRequest<LoginResponse>("POST", "/login", req, { skipAuth: true }),

  getStatus: () => apiRequest<StatusResponse>("GET", "/status"),

  postPosition: (req: PositionRequest) =>
    apiRequest<OkResponse>("POST", "/position", req),

  putMode: (req: ModeRequest) => apiRequest<OkResponse>("PUT", "/mode", req),

  putAuto: (req: AutoRequest) => apiRequest<OkResponse>("PUT", "/auto", req),

  putSettings: (req: SettingsRequest) =>
    apiRequest<OkResponse>("PUT", "/settings", req),

  postSync: () => apiRequest<OkResponse>("POST", "/sync"),

  getDebug: (limit = 20) =>
    apiRequest<DebugResponse>("GET", `/debug?limit=${encodeURIComponent(limit)}`),
};
