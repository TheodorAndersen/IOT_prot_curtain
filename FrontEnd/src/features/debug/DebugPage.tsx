import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowsClockwise, Broadcast, Copy, Usb } from "@phosphor-icons/react";
import { api } from "../../api/endpoints";
import { AppShell } from "../../components/AppShell";
import { Toast } from "../../components/Toast";
import { useDebugPolling, type DebugIntervalMs } from "../../hooks/useDebugPolling";
import { useStatusPolling } from "../../hooks/useStatusPolling";
import { useI18n } from "../../i18n";
import type { Link as LinkKind } from "../../types/api";
import {
  closedToOpenPct,
  formatLocalClock,
  formatLocalDateTime,
  formatRelativeAge,
} from "../../utils/formatters";
import { CommandCard } from "./CommandCard";
import { FlagsInspector } from "./FlagsInspector";
import { UplinkCard } from "./UplinkCard";
import { WireLogTable } from "./WireLogTable";

export function DebugPage() {
  const { t, lang } = useI18n();
  const queryClient = useQueryClient();

  const [intervalMs, setIntervalMs] = useState<DebugIntervalMs>(2000);
  const [confirmSyncOpen, setConfirmSyncOpen] = useState(false);
  const [copiedJson, setCopiedJson] = useState(false);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const debugQ = useDebugPolling(intervalMs, 20);
  const data = debugQ.data;
  const statusQ = useStatusPolling(false);
  const status = statusQ.data;

  const linkMutation = useMutation({
    mutationFn: (link: LinkKind) => api.putLink({ link }),
    onSuccess: (_res, link) => {
      setToastMsg(t("link.switched", { link: link === "usb" ? t("link.usb") : t("link.ttn") }));
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      void queryClient.invalidateQueries({ queryKey: ["debug"] });
    },
    onError: (err) => {
      setToastMsg(
        t("toast.error", { msg: err instanceof Error ? err.message : t("err.network") })
      );
    },
  });
  const shownLink: LinkKind | null = linkMutation.isPending
    ? (linkMutation.variables ?? null)
    : (status?.link ?? null);

  const syncMutation = useMutation({
    mutationFn: () => api.postSync(),
    onSuccess: () => {
      setConfirmSyncOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["debug"] });
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      setToastMsg("POST /sync queued (1 Type 2 downlink)");
    },
    onError: (err) => {
      setConfirmSyncOpen(false);
      setToastMsg(err instanceof Error ? err.message : t("err.network"));
    },
  });

  const copyJson = () => {
    if (!data) return;
    void navigator.clipboard?.writeText(JSON.stringify(data, null, 2)).catch(() => {});
    setCopiedJson(true);
    window.setTimeout(() => setCopiedJson(false), 1500);
  };

  const used = data?.budget.downlinksUsed24h ?? 0;
  const budget = data?.budget.downlinkBudget24h ?? 10;
  const pctUsed = Math.min(100, Math.round((used / Math.max(1, budget)) * 100));
  const budgetSeverity = used >= budget ? "danger" : used >= Math.max(1, budget - 2) ? "warn" : "";

  // USB/TTN switch (only when the server has USB_MQTT_HOST). It needs only /status, so it stays
  // usable even when GET /debug fails.
  const linkCard =
    status?.link != null ? (
      <section className="dcard" aria-labelledby="h-debug-link">
        <h2 id="h-debug-link">
          <span>{t("link.title")}</span>
          <span className={`pill ${status.usbBridgeOnline ? "ok" : "warn"}`}>
            {shownLink === "usb" ? t("link.pillUsb") : t("link.pillTtn")}
          </span>
        </h2>
        <div className="seg seg-lg" role="radiogroup" aria-labelledby="h-debug-link">
          {(["usb", "ttn"] as LinkKind[]).map((l) => (
            <button
              key={l}
              type="button"
              role="radio"
              aria-checked={shownLink === l}
              className={`seg-btn${shownLink === l ? " active" : ""}`}
              disabled={linkMutation.isPending}
              onClick={() => {
                if (status?.link !== l) linkMutation.mutate(l);
              }}
            >
              {l === "usb" ? (
                <Usb size={20} aria-hidden="true" />
              ) : (
                <Broadcast size={20} aria-hidden="true" />
              )}
              <span>{l === "usb" ? t("link.usb") : t("link.ttn")}</span>
            </button>
          ))}
        </div>
        <p className="link-state" style={{ marginTop: 10 }}>
          <span
            className={`link-dot${status.usbBridgeOnline ? " on" : ""}`}
            aria-hidden="true"
          />
          {status.usbBridgeOnline ? t("link.pcOnline") : t("link.pcOffline")}
        </p>
        <p className="group-note" style={{ margin: "6px 0 0" }}>
          {shownLink === "usb" ? t("link.noteUsb") : t("link.noteTtn")}
        </p>
      </section>
    ) : null;

  return (
    <AppShell wide>
      <div className="page-head">
        <h1 className="page-title">{t("debug.title")}</h1>

        <div className="debug-controls">
          <div className="seg" role="radiogroup" aria-label="Auto refresh">
            {(
              [
                [2000, t("debug.refresh2s")],
                [5000, t("debug.refresh5s")],
                [false, t("debug.paused")],
              ] as Array<[DebugIntervalMs, string]>
            ).map(([ms, lbl]) => (
              <button
                key={String(ms)}
                type="button"
                role="radio"
                aria-checked={intervalMs === ms}
                className={`seg-btn${intervalMs === ms ? " active" : ""}`}
                style={{ minHeight: 38, padding: "4px 12px", fontSize: "0.85rem" }}
                onClick={() => setIntervalMs(ms)}
              >
                {lbl}
              </button>
            ))}
          </div>

          <button
            type="button"
            className="btn outline small"
            onClick={() => void debugQ.refetch()}
          >
            <ArrowsClockwise size={16} aria-hidden="true" />
            <span>{t("debug.refreshNow")}</span>
          </button>

          <button
            type="button"
            className="btn primary small"
            onClick={() => setConfirmSyncOpen((v) => !v)}
          >
            {t("debug.forceSync")}
          </button>

          <button
            type="button"
            className="btn ghost small"
            disabled={!data}
            onClick={copyJson}
          >
            <Copy size={16} aria-hidden="true" />
            <span>{copiedJson ? t("debug.copied") : t("debug.copyJson")}</span>
          </button>
        </div>
      </div>

      {confirmSyncOpen && (
        <div className="banner warn" role="region">
          <div className="banner-body">
            <p>{t("debug.forceSyncConfirm")}</p>
          </div>
          <button
            type="button"
            className="btn ghost small"
            onClick={() => setConfirmSyncOpen(false)}
          >
            {t("stage.cancel")}
          </button>
          <button
            type="button"
            className="btn primary small"
            disabled={syncMutation.isPending}
            onClick={() => syncMutation.mutate()}
          >
            {t("debug.confirmSend")}
          </button>
        </div>
      )}

      {data ? (
        <div className="debug-grid">
          {/* Card 1: System, Transport & Budget */}
          <section className="dcard">
            <h2>
              <span>{t("debug.system")}</span>
              <span className={`pill ${data.server.transportConnected ? "ok" : "danger"}`}>
                {data.server.transportMode} ·{" "}
                {data.server.transportConnected ? "Connected" : "Disconnected"}
              </span>
            </h2>
            <dl className="kv-list">
              <dt>Protocol</dt>
              <dd className="mono">
                {data.server.protocolVersion} ·{" "}
                {data.server.confirmedDownlinks ? "confirmed" : "unconfirmed"} ·{" "}
                {data.server.handshakeTimeoutSec}s × {data.server.handshakeMaxAttempts}
              </dd>
              <dt>Timezone</dt>
              <dd className="mono">
                {data.server.scheduleTimezone} ({data.server.localTime})
              </dd>
              <dt>Server UTC</dt>
              <dd className="mono">{data.server.serverTime}</dd>
            </dl>
            <div style={{ marginTop: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.86rem", fontWeight: 600 }}>
                <span>Rolling 24h downlinks</span>
                <span className="mono">
                  {used} / {budget}
                </span>
              </div>
              <div className="progress-track">
                <div
                  className={`progress-fill ${budgetSeverity}`}
                  style={{ width: `${pctUsed}%` }}
                />
              </div>
              <p className="row-sub" style={{ margin: 0 }}>
                Next slot frees:{" "}
                {data.budget.downlinkNextSlotAt
                  ? formatLocalClock(data.budget.downlinkNextSlotAt, lang, true)
                  : "all slots free"}
              </p>
            </div>
          </section>

          {linkCard}

          {/* Card 2: Live Device State & Flags */}
          <section className="dcard">
            <h2>
              <span>{t("debug.deviceState")}</span>
              <span className={`pill ${data.device.settingsInSync ? "ok" : "warn"}`}>
                {data.device.settingsInSync ? "In Sync" : "Out of Sync"}
              </span>
            </h2>
            <dl className="kv-list">
              <dt>TTN ID / DevEUI</dt>
              <dd className="mono">
                {data.device.ttnDeviceId} · {data.device.devEui}
              </dd>
              <dt>Position / Target</dt>
              <dd className="mono">
                {data.device.position === null
                  ? "null (uncal)"
                  : `${closedToOpenPct(data.device.position)}% open (wire: ${data.device.position}% closed)`}{" "}
                /{" "}
                {data.device.targetPosition === null
                  ? "null"
                  : `${closedToOpenPct(data.device.targetPosition)}% open (wire: ${data.device.targetPosition}% closed)`}
              </dd>
              <dt>Modes (Rep/Des/Eff)</dt>
              <dd>
                <span className="mono">
                  {data.device.reportedMode} / {data.device.desiredMode} /{" "}
                  <strong>{data.device.effectiveMode}</strong>
                </span>
                {data.device.reportedMode !== data.device.desiredMode && (
                  <span className="pill warn" style={{ marginLeft: 8 }}>
                    transitioning
                  </span>
                )}
              </dd>
              <dt>Calibrated / Moving</dt>
              <dd>
                {data.device.calibrated ? "Yes" : "No"} / {data.device.moving ? "Moving" : "Idle"}
              </dd>
              <dt>Day / Clock Hint</dt>
              <dd className="mono">
                device={data.device.day ? "day" : "night"} · clockHint=
                {data.device.computedDayHint === null
                  ? "null (sensor)"
                  : data.device.computedDayHint
                    ? "day"
                    : "night"}
              </dd>
              <dt>Cmd ID (Ack / Next)</dt>
              <dd className="mono">
                #{data.device.lastCmdIdAck} / #{data.device.nextCmdId}
              </dd>
              <dt>Last Seen</dt>
              <dd className="mono">
                {formatLocalDateTime(data.device.lastSeenAt, lang)} (
                {formatRelativeAge(data.device.lastSeenAgoSec, lang)})
              </dd>
            </dl>

            <FlagsInspector
              flagsRaw={data.device.flagsRaw}
              flagsHex={data.device.flagsHex}
              decoded={data.device.decodedFlags}
            />

            <details style={{ marginTop: 10 }}>
              <summary className="row-sub" style={{ cursor: "pointer" }}>
                Settings diff (saved vs last confirmed Type 3)
              </summary>
              <pre className="mono" style={{ fontSize: "0.76rem", overflowX: "auto", margin: "6px 0 0" }}>
                {JSON.stringify(
                  {
                    saved: data.device.savedSettings,
                    confirmed: data.device.lastConfirmedAutoSettings,
                  },
                  null,
                  2
                )}
              </pre>
            </details>
          </section>

          {/* Card 3: Last Command Sent & Queue */}
          <CommandCard
            title={t("debug.lastCmd")}
            inFlight={data.inFlightCommand}
            lastSent={data.lastCommandSent}
            queued={data.queuedCommands}
            handshakeTimeoutSec={data.server.handshakeTimeoutSec}
            handshakeMaxAttempts={data.server.handshakeMaxAttempts}
            lang={lang}
          />

          {/* Card 4: Last Uplink Received */}
          <UplinkCard
            title={t("debug.lastUplink")}
            uplink={data.lastUplinkReceived}
            lang={lang}
          />

          {/* Card 5: Unified Wire Traffic Log */}
          <WireLogTable
            title={t("debug.wireLog")}
            commands={data.recentCommands}
            uplinks={data.recentUplinks}
            lang={lang}
          />
        </div>
      ) : (
        <>
          {debugQ.isError && (
            <div className="banner danger" role="alert">
              <div className="banner-body">
                <p>
                  {t("debug.loadError", {
                    msg: debugQ.error instanceof Error ? debugQ.error.message : t("err.network"),
                  })}
                </p>
              </div>
              <button type="button" className="btn ghost small" onClick={() => void debugQ.refetch()}>
                {t("offline.retry")}
              </button>
            </div>
          )}
          {linkCard && <div className="debug-grid">{linkCard}</div>}
        </>
      )}

      <p className="group-note" style={{ marginTop: 14 }}>
        {t("debug.usbNote")}
      </p>

      <Toast message={toastMsg} onDismiss={() => setToastMsg(null)} />
    </AppShell>
  );
}
