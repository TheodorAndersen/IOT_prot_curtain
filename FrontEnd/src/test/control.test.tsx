import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nProvider } from "../i18n";
import { AuthProvider } from "../hooks/useAuth";
import { HomePage } from "../features/control/HomePage";
import { SettingsPage } from "../features/settings/SettingsPage";
import type { StatusResponse } from "../types/api";
import { api } from "../api/endpoints";

function makeStatus(overrides: Partial<StatusResponse> = {}): StatusResponse {
  return {
    position: 50,
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
    ...overrides,
  };
}

function renderWithProviders(ui: React.ReactElement) {
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
          <MemoryRouter>{ui}</MemoryRouter>
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}

describe("HomePage and SettingsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("displays % open in UI while sending wire % closed in POST /position", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue(makeStatus({ position: 20, mode: "manual" }));
    const postPosSpy = vi.spyOn(api, "postPosition").mockResolvedValue({ ok: true });

    renderWithProviders(<HomePage />);
    await screen.findByText(/3 \/ 10 downlinks/i);
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText(/% open/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open fully" }));
    expect(screen.getByText("Target: 100% open")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Move to 100%" }));
    await waitFor(() => expect(postPosSpy).toHaveBeenCalledWith({ position: 0 }));

    fireEvent.click(screen.getByRole("button", { name: "Close fully" }));
    expect(screen.getByText("Target: 0% open")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Move to 0%" }));
    await waitFor(() => expect(postPosSpy).toHaveBeenCalledWith({ position: 100 }));

    fireEvent.click(screen.getByRole("button", { name: "Move halfway" }));
    expect(screen.getByText("Target: 50% open")).toBeInTheDocument();
    const confirmBtn = await screen.findByRole("button", { name: /Move to 50%/i });
    fireEvent.click(confirmBtn);
    await waitFor(() => expect(postPosSpy).toHaveBeenCalledWith({ position: 50 }));
  });

  it("shows uncalibrated guard banner and disables presets when position is null", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue(
      makeStatus({ position: null, calibrated: false })
    );
    renderWithProviders(<HomePage />);
    await screen.findByText(/Curtain is not calibrated/i);
    expect(screen.getByRole("button", { name: "Open fully" })).toBeDisabled();
  });

  it("clamps outdoor thresholds so close < open in SettingsPage", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue(
      makeStatus({ outsideThresholdOpen: 30, outsideThresholdClose: 25 })
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("30%");
    const closeSlider = screen.getByLabelText(/Close when outside light is below/i);
    fireEvent.change(closeSlider, { target: { value: "35" } });
    expect((closeSlider as HTMLInputElement).value).toBe("29");
    expect(screen.getByRole("alert")).toHaveTextContent(/must be lower than the open limit/i);
  });

  it("stages mode switch before calling PUT /mode and allows cancel or toggling back", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue(makeStatus({ mode: "manual" }));
    const putModeSpy = vi.spyOn(api, "putMode").mockResolvedValue({ ok: true });

    renderWithProviders(<HomePage />);
    await screen.findByText(/3 \/ 10 downlinks/i);

    fireEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    expect(putModeSpy).not.toHaveBeenCalled();
    expect(screen.getByText(/Switch to Automatic mode\?/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Keep a steady light level/i)).toBeInTheDocument();

    // Toggling back to Manual cancels with zero downlinks
    fireEvent.click(screen.getByRole("radio", { name: "Manual" }));
    expect(screen.queryByText(/Switch to Automatic mode\?/i)).not.toBeInTheDocument();
    expect(putModeSpy).not.toHaveBeenCalled();

    // Staging again and clicking Cancel also keeps zero calls
    fireEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/Switch to Automatic mode\?/i)).not.toBeInTheDocument();
    expect(putModeSpy).not.toHaveBeenCalled();

    // Staging and confirming calls PUT /mode once
    fireEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    fireEvent.click(await screen.findByRole("button", { name: "Switch to Automatic" }));
    await waitFor(() => expect(putModeSpy).toHaveBeenCalledWith({ mode: "automatic" }));
  });

  it("stages combined mode switch, auto rule, and inline outdoor threshold edits in minimal-downlink order", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue(
      makeStatus({
        mode: "manual",
        autoOpen: 0,
        outsideThresholdOpen: 80,
        outsideThresholdClose: 10,
      })
    );
    const callOrder: string[] = [];
    const putAutoSpy = vi.spyOn(api, "putAuto").mockImplementation(async () => {
      callOrder.push("auto");
      return { ok: true };
    });
    const putSettingsSpy = vi.spyOn(api, "putSettings").mockImplementation(async () => {
      callOrder.push("settings");
      return { ok: true };
    });
    const putModeSpy = vi.spyOn(api, "putMode").mockImplementation(async () => {
      callOrder.push("mode");
      return { ok: true };
    });

    renderWithProviders(<HomePage />);
    await screen.findByText(/3 \/ 10 downlinks/i);

    fireEvent.click(screen.getByRole("radio", { name: "Automatic" }));
    fireEvent.click(screen.getByRole("radio", { name: "At sunrise" }));
    const openSlider = screen.getByLabelText(/Open when outside light is above/i);
    fireEvent.change(openSlider, { target: { value: "75" } });

    expect(putAutoSpy).not.toHaveBeenCalled();
    expect(putSettingsSpy).not.toHaveBeenCalled();
    expect(putModeSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm & Send" }));
    await waitFor(() => expect(putModeSpy).toHaveBeenCalledTimes(1));

    expect(callOrder).toEqual(["auto", "settings", "mode"]);
    expect(putAutoSpy).toHaveBeenCalledWith({
      autoOpen: 1,
      autoOpenTime: "07:30",
      autoClose: 1,
      autoCloseTime: "21:00",
      maintainLight: 0,
      targetLightLevel: 60,
    });
    expect(putSettingsSpy).toHaveBeenCalledWith({
      outsideThresholdOpen: 75,
      outsideThresholdClose: 10,
    });
    expect(putModeSpy).toHaveBeenCalledWith({ mode: "automatic" });
  });

  it("stages indoor light level and clamped inline outdoor thresholds while in automatic mode", async () => {
    vi.spyOn(api, "getStatus").mockResolvedValue(
      makeStatus({
        mode: "automatic",
        maintainLight: 1,
        targetLightLevel: 60,
        autoClose: 1,
        outsideThresholdOpen: 40,
        outsideThresholdClose: 20,
      })
    );
    const putAutoSpy = vi.spyOn(api, "putAuto").mockResolvedValue({ ok: true });
    const putSettingsSpy = vi.spyOn(api, "putSettings").mockResolvedValue({ ok: true });
    const putModeSpy = vi.spyOn(api, "putMode").mockResolvedValue({ ok: true });

    renderWithProviders(<HomePage />);
    const lightSlider = await screen.findByLabelText(/^Light level$/i);
    await waitFor(() => expect((lightSlider as HTMLInputElement).value).toBe("60"));

    fireEvent.change(lightSlider, { target: { value: "70" } });
    const closeSlider = screen.getByLabelText(/Close when outside light is below/i);
    fireEvent.change(closeSlider, { target: { value: "45" } });

    expect((closeSlider as HTMLInputElement).value).toBe("39");
    expect(screen.getByRole("alert")).toHaveTextContent(/must be lower than the open limit/i);
    expect(putAutoSpy).not.toHaveBeenCalled();
    expect(putSettingsSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm & Send" }));
    await waitFor(() => expect(putSettingsSpy).toHaveBeenCalledTimes(1));

    expect(putAutoSpy).toHaveBeenCalledWith({
      autoOpen: 0,
      autoOpenTime: "07:30",
      autoClose: 1,
      autoCloseTime: "21:00",
      maintainLight: 1,
      targetLightLevel: 70,
    });
    expect(putSettingsSpy).toHaveBeenCalledWith({
      outsideThresholdOpen: 40,
      outsideThresholdClose: 39,
    });
    expect(putModeSpy).not.toHaveBeenCalled();
  });
});
