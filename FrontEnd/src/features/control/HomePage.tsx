import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Hand, SunHorizon, WarningCircle } from "@phosphor-icons/react";
import { api } from "../../api/endpoints";
import { AppShell } from "../../components/AppShell";
import { BudgetBadge } from "../../components/BudgetBadge";
import { CurtainWindow } from "../../components/CurtainWindow";
import { SaveBar } from "../../components/SaveBar";
import { Toast } from "../../components/Toast";
import { useStatusPolling } from "../../hooks/useStatusPolling";
import { useI18n } from "../../i18n";
import type {
  AutoRequest,
  CurtainMode,
  SettingsRequest,
  StatusResponse,
} from "../../types/api";
import { closedToOpenPct } from "../../utils/formatters";
import { storage } from "../../utils/storage";
import { AutoRulesPanel } from "./AutoRulesPanel";
import { PositionControls } from "./PositionControls";

const DEFAULT_AUTO: AutoRequest = {
  autoOpen: 0,
  autoOpenTime: "07:30",
  autoClose: 1,
  autoCloseTime: "21:00",
  maintainLight: 0,
  targetLightLevel: 60,
};

const DEFAULT_THR: SettingsRequest = {
  outsideThresholdOpen: 80,
  outsideThresholdClose: 10,
};

function toAutoReq(s: StatusResponse): AutoRequest {
  return {
    autoOpen: s.autoOpen,
    autoOpenTime: s.autoOpenTime,
    autoClose: s.autoClose,
    autoCloseTime: s.autoCloseTime,
    maintainLight: s.maintainLight,
    targetLightLevel: s.targetLightLevel,
  };
}

function isAutoEqual(a: AutoRequest, s: StatusResponse): boolean {
  return (
    a.autoOpen === s.autoOpen &&
    a.autoOpenTime === s.autoOpenTime &&
    a.autoClose === s.autoClose &&
    a.autoCloseTime === s.autoCloseTime &&
    a.maintainLight === s.maintainLight &&
    a.targetLightLevel === s.targetLightLevel
  );
}

function toThrReq(s: StatusResponse): SettingsRequest {
  return {
    outsideThresholdOpen: s.outsideThresholdOpen,
    outsideThresholdClose: s.outsideThresholdClose,
  };
}

function isThrEqual(a: SettingsRequest, s: StatusResponse): boolean {
  return (
    a.outsideThresholdOpen === s.outsideThresholdOpen &&
    a.outsideThresholdClose === s.outsideThresholdClose
  );
}

interface ApplyChangesVars {
  targetMode: CurtainMode | null;
  saveAuto: boolean;
  saveThr: boolean;
}

export function HomePage() {
  const { lang, t, pct, posText } = useI18n();
  const queryClient = useQueryClient();

  const [stagedPos, setStagedPos] = useState<number | null>(null);
  const [stagedMode, setStagedMode] = useState<CurtainMode | null>(null);
  const [dragPos, setDragPos] = useState<number | null>(null);
  const [awaitingMove, setAwaitingMove] = useState(false);
  const [awaitingSync, setAwaitingSync] = useState(false);

  const [autoDirty, setAutoDirty] = useState(false);
  const [autoDraft, setAutoDraft] = useState<AutoRequest>(DEFAULT_AUTO);
  const [thrDirty, setThrDirty] = useState(false);
  const [thrDraft, setThrDraft] = useState<SettingsRequest>(DEFAULT_THR);
  const [thrClampErr, setThrClampErr] = useState("");
  const [autoSavedFlash, setAutoSavedFlash] = useState(false);

  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [toastAction, setToastAction] = useState<{ label: string; fn: () => void } | null>(null);
  const lastModeRef = useRef<CurtainMode | null>(null);
  const appModeChangeAtRef = useRef<number>(0);

  const instantSend = storage.get<boolean>("instantSend", false);
  const blindName = storage.get<string>("name", "") || t("name.default");

  const statusQ = useStatusPolling(awaitingMove || awaitingSync);
  const status = statusQ.data;

  // Sync Auto and threshold drafts from server only when user is NOT editing
  useEffect(() => {
    if (status && !autoDirty) {
      setAutoDraft(toAutoReq(status));
    }
  }, [status, autoDirty]);

  useEffect(() => {
    if (status && !thrDirty) {
      setThrDraft(toThrReq(status));
    }
  }, [status, thrDirty]);

  useEffect(() => {
    if (status && stagedMode === status.mode) {
      setStagedMode(null);
    }
  }, [status, stagedMode]);

  // Clear awaitingMove once motor settles or mode becomes automatic
  useEffect(() => {
    if (!status) return;
    if (!status.moving && status.targetPosition === null) {
      setAwaitingMove(false);
    }
    const prev = lastModeRef.current;
    if (
      prev === "automatic" &&
      status.mode === "manual" &&
      Date.now() - appModeChangeAtRef.current > 10000
    ) {
      setToastMsg(t("toast.external"));
      setToastAction({
        label: t("toast.autoOn"),
        fn: () => handleModeSelect("automatic"),
      });
    }
    lastModeRef.current = status.mode;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const showToast = (msg: string, action?: { label: string; fn: () => void }) => {
    setToastMsg(msg);
    setToastAction(action ?? null);
    window.setTimeout(() => {
      setToastMsg(null);
      setToastAction(null);
    }, action ? 7000 : 4500);
  };

  const applyChangesMutation = useMutation({
    mutationFn: async ({ targetMode, saveAuto, saveThr }: ApplyChangesVars) => {
      if (targetMode === "automatic") {
        if (saveAuto) await api.putAuto(autoDraft);
        if (saveThr) await api.putSettings(thrDraft);
        await api.putMode({ mode: "automatic" });
      } else if (targetMode === "manual") {
        await api.putMode({ mode: "manual" });
        if (saveAuto) await api.putAuto(autoDraft);
        if (saveThr) await api.putSettings(thrDraft);
      } else {
        await Promise.all([
          saveAuto ? api.putAuto(autoDraft) : Promise.resolve(),
          saveThr ? api.putSettings(thrDraft) : Promise.resolve(),
        ]);
      }
    },
    onMutate: ({ targetMode }) => {
      if (targetMode !== null) {
        appModeChangeAtRef.current = Date.now();
      }
    },
    onSuccess: () => {
      setStagedMode(null);
      setAutoDirty(false);
      setThrDirty(false);
      setThrClampErr("");
      setAutoSavedFlash(true);
      setAwaitingSync(true);
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      window.setTimeout(() => void queryClient.invalidateQueries({ queryKey: ["status"] }), 400);
      window.setTimeout(() => setAwaitingSync(false), 2500);
      window.setTimeout(() => setAutoSavedFlash(false), 1800);
    },
    onError: (err) => {
      showToast(t("toast.error", { msg: err instanceof Error ? err.message : t("err.network") }));
    },
  });

  const posMutation = useMutation({
    mutationFn: (position: number) => api.postPosition({ position }),
    onSuccess: (_res, position) => {
      const wasAuto = status?.mode === "automatic";
      appModeChangeAtRef.current = Date.now();
      setStagedPos(null);
      setStagedMode(null);
      setAwaitingMove(true);
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      if (wasAuto) {
        showToast(t("toast.toManual"), {
          label: t("toast.autoOn"),
          fn: () => handleModeSelect("automatic"),
        });
      } else {
        showToast(
          t("status.movingTo", {
            t: pct(closedToOpenPct(position)),
            a: pct(closedToOpenPct(status?.position ?? position)),
          })
        );
      }
    },
    onError: (err) => {
      setStagedPos(null);
      setAwaitingMove(false);
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      showToast(t("toast.error", { msg: err instanceof Error ? err.message : t("err.network") }));
    },
  });

  const handleModeSelect = (nextMode: CurtainMode) => {
    if (!status) return;
    setStagedPos(null);
    setAutoSavedFlash(false);
    if (nextMode === status.mode) {
      setStagedMode(null);
      return;
    }
    if (instantSend && !autoDirty && !thrDirty) {
      applyChangesMutation.mutate({
        targetMode: nextMode,
        saveAuto: false,
        saveThr: false,
      });
      return;
    }
    setStagedMode(nextMode);
  };

  const updateThresholds = (which: "open" | "close", val: number) => {
    let nextOpen = which === "open" ? val : thrDraft.outsideThresholdOpen;
    let nextClose = which === "close" ? val : thrDraft.outsideThresholdClose;
    let err = "";
    if (which === "open" && nextOpen <= nextClose) {
      nextOpen = Math.min(100, nextClose + 1);
      err = t("sun.err");
    } else if (which === "close" && nextClose >= nextOpen) {
      nextClose = Math.max(0, nextOpen - 1);
      err = t("sun.err");
    }
    const nextThr: SettingsRequest = {
      outsideThresholdOpen: nextOpen,
      outsideThresholdClose: nextClose,
    };
    setThrDraft(nextThr);
    setThrClampErr(err);
    setStagedPos(null);
    setAutoSavedFlash(false);
    setThrDirty(!status || !isThrEqual(nextThr, status));
  };

  const isUncalibrated = Boolean(status && (!status.calibrated || status.position === null));
  const actualPos = status?.position ?? null;
  const activeTarget = status?.moving ? status.targetPosition : null;
  const shownPos = dragPos ?? stagedPos ?? activeTarget ?? actualPos;
  const showGhost =
    actualPos !== null &&
    ((stagedPos !== null && stagedPos !== actualPos) ||
      (status?.moving === true && activeTarget !== null && activeTarget !== actualPos));

  const hasModeChange = Boolean(status && stagedMode !== null && stagedMode !== status.mode);
  const displayedMode: CurtainMode = stagedMode ?? status?.mode ?? "automatic";
  const hasControlChanges = hasModeChange || autoDirty || thrDirty;

  const handlePositionPick = (p: number) => {
    if (isUncalibrated || !status) return;
    if (instantSend) {
      posMutation.mutate(p);
    } else {
      setStagedMode(null);
      setStagedPos(p);
    }
  };

  let statusLine = t(instantSend ? "status.hintInstant" : "status.hint");
  if (stagedPos !== null && actualPos !== null) {
    statusLine = t("status.staged", {
      t: pct(closedToOpenPct(stagedPos)),
      a: pct(closedToOpenPct(actualPos)),
    });
  } else if (status?.moving && activeTarget !== null && actualPos !== null) {
    statusLine = t("status.movingTo", {
      t: pct(closedToOpenPct(activeTarget)),
      a: pct(closedToOpenPct(actualPos)),
    });
  } else if (status?.mode === "automatic") {
    const open = status.autoOpen
      ? t("auto.openSun")
      : t("auto.openTime", { t: status.autoOpenTime });
    const close = status.autoClose
      ? t("auto.closeSun")
      : t("auto.closeTime", { t: status.autoCloseTime });
    statusLine =
      `${open}, ${close}.` +
      (status.maintainLight ? ` ${t("auto.keepSummary", { l: pct(status.targetLightLevel) })}` : "");
  }

  return (
    <AppShell>
      <div className="top-header">
        <div>
          <p className="blind-name">{blindName}</p>
          <p className="readout" aria-live="polite">
            {shownPos === null ? (
              <span>{t("pos.uncalibrated")}</span>
            ) : shownPos === 0 ? (
              <span>{t("pos.open")}</span>
            ) : shownPos === 100 ? (
              <span>{t("pos.closed")}</span>
            ) : (
              <>
                <span>{closedToOpenPct(shownPos)}</span>
                <span className="word">{lang === "da" ? " % " : "% "}{t("pos.word")}</span>
              </>
            )}
          </p>
        </div>
        {status && (
          <BudgetBadge
            used={status.downlinksUsed24h}
            budget={status.downlinkBudget24h}
            nextSlotAt={status.downlinkNextSlotAt}
          />
        )}
      </div>

      <p className="status-line">{statusLine}</p>

      {isUncalibrated && (
        <div className="banner warn" role="alert">
          <WarningCircle size={22} weight="bold" aria-hidden="true" />
          <div className="banner-body">
            <p>
              <strong>{t("uncal.title")}</strong>
            </p>
            <p style={{ marginTop: 4 }}>{t("uncal.steps")}</p>
            <p style={{ marginTop: 6 }}>
              <Link to="/debug" style={{ textDecoration: "underline", fontWeight: 600 }}>
                {t("uncal.syncLink")}
              </Link>
            </p>
          </div>
        </div>
      )}

      <div className="home-grid">
        <div>
          <CurtainWindow
            shownPosition={shownPos}
            actualPosition={actualPos}
            showGhost={showGhost}
            loading={!status}
            disabled={!status || isUncalibrated}
            onDragLive={setDragPos}
            onDragCommit={handlePositionPick}
          />

          <PositionControls
            shownPosition={shownPos}
            isMoving={Boolean(status?.moving)}
            disabled={!status || isUncalibrated}
            onSelectPreset={handlePositionPick}
          />
        </div>

        <div>
          <section aria-labelledby="mode-heading">
            <h2 className="group-title" id="mode-heading">
              {t("mode.title")}
            </h2>
            <div className="seg seg-lg" role="radiogroup" aria-labelledby="mode-heading">
              <button
                type="button"
                role="radio"
                aria-checked={displayedMode === "manual"}
                className={`seg-btn${displayedMode === "manual" ? " active" : ""}`}
                disabled={!status || applyChangesMutation.isPending}
                onClick={() => handleModeSelect("manual")}
              >
                <Hand size={20} aria-hidden="true" />
                <span>{t("mode.manual")}</span>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={displayedMode === "automatic"}
                className={`seg-btn${displayedMode === "automatic" ? " active" : ""}`}
                disabled={!status || applyChangesMutation.isPending}
                onClick={() => handleModeSelect("automatic")}
              >
                <SunHorizon size={20} aria-hidden="true" />
                <span>{t("mode.auto")}</span>
              </button>
            </div>
            <p className="group-note">
              {hasModeChange
                ? stagedMode === "automatic"
                  ? t("mode.pendingAuto")
                  : t("mode.pendingManual")
                : displayedMode === "automatic"
                  ? t("mode.autoHelp")
                  : t("mode.manualHelp")}
            </p>
          </section>

          {displayedMode === "automatic" && (
            <div style={{ marginTop: 22 }}>
              <AutoRulesPanel
                draft={autoDraft}
                outsideThresholdOpen={thrDraft.outsideThresholdOpen}
                outsideThresholdClose={thrDraft.outsideThresholdClose}
                thresholdClampError={thrClampErr}
                onChange={(next) => {
                  setAutoDraft(next);
                  setStagedPos(null);
                  setAutoDirty(!status || !isAutoEqual(next, status));
                  setAutoSavedFlash(false);
                }}
                onOutsideThresholdOpenChange={(val) => updateThresholds("open", val)}
                onOutsideThresholdCloseChange={(val) => updateThresholds("close", val)}
              />
            </div>
          )}
        </div>
      </div>

      {/* Staged position confirmation bar takes priority over control changes SaveBar */}
      <SaveBar
        visible={stagedPos !== null && !isUncalibrated}
        text={
          stagedPos !== null
            ? status?.mode === "automatic"
              ? t("stage.switchAuto", { p: posText(stagedPos) })
              : t("stage.target", { p: pct(closedToOpenPct(stagedPos)) })
            : ""
        }
        cancelLabel={t("stage.cancel")}
        confirmLabel={
          posMutation.isPending
            ? t("stage.sending")
            : status?.mode === "automatic"
              ? t("stage.confirmSwitch")
              : t("stage.moveTo", { p: stagedPos !== null ? pct(closedToOpenPct(stagedPos)) : "" })
        }
        busy={posMutation.isPending}
        confirmDisabled={isUncalibrated}
        onCancel={() => setStagedPos(null)}
        onConfirm={() => {
          if (stagedPos !== null) posMutation.mutate(stagedPos);
        }}
      />

      <SaveBar
        visible={stagedPos === null && (hasControlChanges || autoSavedFlash)}
        text={
          autoSavedFlash
            ? t("save.saved")
            : applyChangesMutation.isPending
              ? t("stage.sending")
              : hasModeChange && (autoDirty || thrDirty)
                ? stagedMode === "automatic"
                  ? t("stage.modeAutoWithChanges")
                  : t("stage.modeManualWithChanges")
                : hasModeChange
                  ? stagedMode === "automatic"
                    ? t("stage.modeAuto")
                    : t("stage.modeManual")
                  : t("save.unsaved")
        }
        cancelLabel={
          hasModeChange && !autoDirty && !thrDirty ? t("stage.cancel") : t("save.discard")
        }
        confirmLabel={
          applyChangesMutation.isPending
            ? t("stage.sending")
            : hasModeChange && !autoDirty && !thrDirty
              ? stagedMode === "automatic"
                ? t("stage.confirmAuto")
                : t("stage.confirmManual")
              : t("stage.confirmSend")
        }
        busy={applyChangesMutation.isPending}
        saved={autoSavedFlash}
        onCancel={() => {
          setStagedMode(null);
          if (status) {
            setAutoDraft(toAutoReq(status));
            setThrDraft(toThrReq(status));
          }
          setAutoDirty(false);
          setThrDirty(false);
          setThrClampErr("");
        }}
        onConfirm={() => {
          if (
            (displayedMode === "automatic" || autoDirty) &&
            ((autoDraft.autoOpen === 0 && !autoDraft.autoOpenTime) ||
              (autoDraft.autoClose === 0 && !autoDraft.autoCloseTime))
          ) {
            showToast(t("auto.timeMissing"));
            return;
          }
          applyChangesMutation.mutate({
            targetMode: hasModeChange ? stagedMode : null,
            saveAuto: autoDirty,
            saveThr: thrDirty,
          });
        }}
      />

      <Toast
        message={toastMsg}
        actionLabel={toastAction?.label}
        onAction={toastAction?.fn}
        onDismiss={() => {
          setToastMsg(null);
          setToastAction(null);
        }}
      />
    </AppShell>
  );
}
