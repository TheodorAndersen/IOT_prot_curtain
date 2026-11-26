export type CurtainMode = "manual" | "automatic";

export type CommandKind =
  | "set_position"
  | "set_mode"
  | "set_auto_settings"
  | "set_schedule";

export type CommandStatus =
  | "QUEUED"
  | "IN_FLIGHT"
  | "CONFIRMED"
  | "REJECTED"
  | "NOT_DELIVERED"
  | "SUPERSEDED";

export type UplinkReason =
  | "heartbeat"
  | "startup"
  | "button"
  | "app_command"
  | "auto"
  | "schedule"
  | "calibrated"
  | "rejected";

export interface HealthResponse {
  ok: boolean;
  transportConnected: boolean;
  protocolVersion: "v3" | "v4" | string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  token: string;
}

export interface OkResponse {
  ok: boolean;
}

export interface PositionRequest {
  position: number;
}

export interface ModeRequest {
  mode: CurtainMode;
}

export interface AutoRequest {
  autoOpen: 0 | 1;
  autoOpenTime: string;
  autoClose: 0 | 1;
  autoCloseTime: string;
  maintainLight: 0 | 1;
  targetLightLevel: number;
}

export interface SettingsRequest {
  outsideThresholdOpen: number;
  outsideThresholdClose: number;
}

export interface StatusResponse {
  position: number | null;
  mode: CurtainMode;
  autoOpen: 0 | 1;
  autoOpenTime: string;
  autoClose: 0 | 1;
  autoCloseTime: string;
  maintainLight: 0 | 1;
  targetLightLevel: number;
  outsideThresholdOpen: number;
  outsideThresholdClose: number;
  moving: boolean;
  calibrated: boolean;
  day: boolean;
  targetPosition: number | null;
  lastSeenAt: string | null;
  downlinksUsed24h: number;
  downlinkBudget24h: number;
  downlinkNextSlotAt: string | null;
}

export interface DecodedFlags {
  automatic: boolean;
  moving: boolean;
  calibrated: boolean;
  day: boolean;
  openBySun: boolean;
  closeBySun: boolean;
  keepIndoorLight: boolean;
}

export interface DebugCommand {
  id: number;
  cmdId: number | null;
  kind: CommandKind;
  args: Record<string, unknown>;
  status: CommandStatus;
  attempts: number;
  wireFport: number | null;
  wirePayloadHex: string | null;
  description: string;
  createdAt: string;
  sentAt: string | null;
  resolvedAt: string | null;
  roundTripMs: number | null;
}

export interface DebugUplink {
  id: number;
  receivedAt: string;
  ageSec: number;
  lastCmdId: number;
  reason: UplinkReason | string;
  mode: CurtainMode;
  flags: number;
  flagsHex: string;
  decodedFlags: DecodedFlags;
  position: number | null;
  rawFport: number;
  rawPayloadHex: string;
  description: string;
}

export interface DebugResponse {
  server: {
    serverTime: string;
    scheduleTimezone: string;
    localTime: string;
    transportMode: "ttn_mqtt" | "mock";
    transportConnected: boolean;
    protocolVersion: "v3" | "v4" | string;
    confirmedDownlinks: boolean;
    handshakeTimeoutSec: number;
    handshakeMaxAttempts: number;
  };
  device: {
    ttnDeviceId: string;
    devEui: string;
    name: string;
    position: number | null;
    targetPosition: number | null;
    reportedMode: CurtainMode;
    desiredMode: CurtainMode;
    effectiveMode: CurtainMode;
    moving: boolean;
    calibrated: boolean;
    day: boolean;
    computedDayHint: boolean | null;
    flagsRaw: number;
    flagsHex: string;
    decodedFlags: DecodedFlags;
    lastReason: string | null;
    lastCmdIdAck: number;
    nextCmdId: number;
    lastSeenAt: string | null;
    lastSeenAgoSec: number | null;
    settingsInSync: boolean;
    savedSettings: {
      autoOpen: 0 | 1;
      autoOpenTime: string;
      autoClose: 0 | 1;
      autoCloseTime: string;
      maintainLight: 0 | 1;
      targetLightLevel: number;
      outsideThresholdOpen: number;
      outsideThresholdClose: number;
    };
    lastConfirmedAutoSettings: {
      autoOpen: 0 | 1;
      autoClose: 0 | 1;
      maintainLight: 0 | 1;
      targetLightLevel: number;
      outsideThresholdOpen: number;
      outsideThresholdClose: number;
    } | null;
  };
  budget: {
    downlinksUsed24h: number;
    downlinkBudget24h: number;
    downlinkNextSlotAt: string | null;
  };
  inFlightCommand: DebugCommand | null;
  queuedCommands: DebugCommand[];
  lastCommandSent: DebugCommand | null;
  lastUplinkReceived: DebugUplink | null;
  recentCommands: DebugCommand[];
  recentUplinks: DebugUplink[];
}
