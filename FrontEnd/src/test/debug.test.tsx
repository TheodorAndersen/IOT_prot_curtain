import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nProvider } from "../i18n";
import { AuthProvider } from "../hooks/useAuth";
import { DebugPage } from "../features/debug/DebugPage";
import { api } from "../api/endpoints";
import type { DebugResponse } from "../types/api";

const MOCK_DEBUG: DebugResponse = {
  server: {
    serverTime: "2026-11-26T02:45:10Z",
    scheduleTimezone: "Europe/Copenhagen",
    localTime: "2026-11-26T03:45:10+01:00",
    transportMode: "ttn_mqtt",
    transportConnected: true,
    protocolVersion: "v4",
    confirmedDownlinks: false,
    handshakeTimeoutSec: 25,
    handshakeMaxAttempts: 2,
  },
  device: {
    ttnDeviceId: "curtain-01",
    devEui: "0004A30B00E8F83E",
    name: "Roller curtain",
    position: 40,
    targetPosition: null,
    reportedMode: "manual",
    desiredMode: "manual",
    effectiveMode: "manual",
    moving: false,
    calibrated: true,
    day: false,
    computedDayHint: false,
    flagsRaw: 36,
    flagsHex: "0x24",
    decodedFlags: {
      automatic: false,
      moving: false,
      calibrated: true,
      day: false,
      openBySun: false,
      closeBySun: true,
      keepIndoorLight: false,
    },
    lastReason: "app_command",
    lastCmdIdAck: 22,
    nextCmdId: 23,
    lastSeenAt: "2026-11-26T02:44:52Z",
    lastSeenAgoSec: 18,
    settingsInSync: true,
    savedSettings: {
      autoOpen: 0,
      autoOpenTime: "07:30",
      autoClose: 1,
      autoCloseTime: "21:00",
      maintainLight: 0,
      targetLightLevel: 60,
      outsideThresholdOpen: 80,
      outsideThresholdClose: 10,
    },
    lastConfirmedAutoSettings: {
      autoOpen: 0,
      autoClose: 1,
      maintainLight: 0,
      targetLightLevel: 60,
      outsideThresholdOpen: 80,
      outsideThresholdClose: 10,
    },
  },
  budget: {
    downlinksUsed24h: 3,
    downlinkBudget24h: 10,
    downlinkNextSlotAt: "2026-11-27T01:12:04Z",
  },
  inFlightCommand: null,
  queuedCommands: [],
  lastCommandSent: {
    id: 48,
    cmdId: 22,
    kind: "set_position",
    args: { position: 40 },
    status: "CONFIRMED",
    attempts: 1,
    wireFport: 1,
    wirePayloadHex: "160128",
    description: "#22 go to 40 % closed",
    createdAt: "2026-11-26T02:44:40Z",
    sentAt: "2026-11-26T02:44:40Z",
    resolvedAt: "2026-11-26T02:44:42Z",
    roundTripMs: 2140,
  },
  lastUplinkReceived: {
    id: 105,
    receivedAt: "2026-11-26T02:44:52Z",
    ageSec: 18,
    lastCmdId: 22,
    reason: "app_command",
    mode: "manual",
    flags: 36,
    flagsHex: "0x24",
    decodedFlags: {
      automatic: false,
      moving: false,
      calibrated: true,
      day: false,
      openBySun: false,
      closeBySun: true,
      keepIndoorLight: false,
    },
    position: 40,
    rawFport: 1,
    rawPayloadHex: "16032428",
    description: "#22 app_command · 40 % closed · manual",
  },
  recentCommands: [
    {
      id: 48,
      cmdId: 22,
      kind: "set_position",
      args: { position: 40 },
      status: "CONFIRMED",
      attempts: 1,
      wireFport: 1,
      wirePayloadHex: "160128",
      description: "#22 go to 40 % closed",
      createdAt: "2026-11-26T02:44:40Z",
      sentAt: "2026-11-26T02:44:40Z",
      resolvedAt: "2026-11-26T02:44:42Z",
      roundTripMs: 2140,
    },
  ],
  recentUplinks: [
    {
      id: 105,
      receivedAt: "2026-11-26T02:44:52Z",
      ageSec: 18,
      lastCmdId: 22,
      reason: "app_command",
      mode: "manual",
      flags: 36,
      flagsHex: "0x24",
      decodedFlags: {
        automatic: false,
        moving: false,
        calibrated: true,
        day: false,
        openBySun: false,
        closeBySun: true,
        keepIndoorLight: false,
      },
      position: 40,
      rawFport: 1,
      rawPayloadHex: "16032428",
      description: "#22 app_command · 40 % closed · manual",
    },
  ],
};

function renderDebug() {
  localStorage.clear();
  localStorage.setItem("curtain.token", JSON.stringify("test-token"));
  localStorage.setItem("curtain.lang", JSON.stringify("en"));
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <AuthProvider>
          <MemoryRouter>
            <DebugPage />
          </MemoryRouter>
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe("DebugPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders last command sent, last uplink received, flags, and wire log", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue({
      position: 40,
      mode: "manual",
      autoOpen: 0,
      autoOpenTime: "07:30",
      autoClose: 1,
      autoCloseTime: "21:00",
      maintainLight: 0,
      targetLightLevel: 60,
      outsideThresholdOpen: 80,
      outsideThresholdClose: 10,
      moving: false,
      calibrated: true,
      day: false,
      targetPosition: null,
      lastSeenAt: "2026-11-26T02:44:52Z",
      downlinksUsed24h: 3,
      downlinkBudget24h: 10,
      downlinkNextSlotAt: null,
    });
    vi.spyOn(api, "getDebug").mockResolvedValue(MOCK_DEBUG);
    const syncSpy = vi.spyOn(api, "postSync").mockResolvedValue({ ok: true });

    renderDebug();
    expect((await screen.findAllByText(/16 01 28/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/16 03 24 28/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/60% open \(wire: 40% closed\)/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2140 ms/).length).toBeGreaterThan(0);
    expect(screen.getByText(/In Sync/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Force State Sync/i }));
    fireEvent.click(await screen.findByRole("button", { name: /Send 1 downlink/i }));
    await waitFor(() => expect(syncSpy).toHaveBeenCalledTimes(1));
  });
});
