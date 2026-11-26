import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCircle,
  CircleNotch,
  Stop,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "../../api/endpoints";
import { AppShell } from "../../components/AppShell";
import { Toast } from "../../components/Toast";
import { useStatusPolling } from "../../hooks/useStatusPolling";
import { useI18n, type I18nKey } from "../../i18n";

type Phase = "intro" | "sending" | "bottom" | "top" | "done" | "failed";
type BoardKey = "up" | "stop" | "down";

const STEPS: Array<{ phase: Phase; label: I18nKey }> = [
  { phase: "sending", label: "cal.stepReady" },
  { phase: "bottom", label: "cal.stepBottom" },
  { phase: "top", label: "cal.stepTop" },
  { phase: "done", label: "cal.stepDone" },
];
const ORDER: Phase[] = ["intro", "sending", "bottom", "top", "done"];

/** The three buttons on the blind, with the ones to press now highlighted and numbered. */
function BoardButtons({ order }: { order: BoardKey[] }) {
  const { t } = useI18n();
  const keys: Array<{ key: BoardKey; icon: ReactNode; label: string }> = [
    { key: "up", icon: <ArrowUp size={26} weight="bold" />, label: t("cal.btnUp") },
    { key: "stop", icon: <Stop size={24} weight="fill" />, label: t("cal.btnStop") },
    { key: "down", icon: <ArrowDown size={26} weight="bold" />, label: t("cal.btnDown") },
  ];
  return (
    <div className="board-buttons" aria-hidden="true">
      {keys.map(({ key, icon, label }) => {
        const n = order.indexOf(key);
        return (
          <div key={key} className={`board-btn${n >= 0 ? " hl" : ""}`}>
            <span className="board-btn-face">{icon}</span>
            <span className="board-btn-label">{label}</span>
            {n >= 0 && <span className="board-btn-order">{n + 1}</span>}
          </div>
        );
      })}
    </div>
  );
}

export function CalibratePage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [sawProgress, setSawProgress] = useState(false);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const statusQ = useStatusPolling(true);
  const status = statusQ.data;
  const step = status?.calibrationStep ?? null;
  const viaTtn = status?.link === "ttn" || status?.link == null;

  useEffect(() => {
    if (step === "bottom" || step === "top") setSawProgress(true);
  }, [step]);

  const start = useMutation({
    mutationFn: () => api.postCalibrate(),
    onMutate: () => {
      setStartedAt(Date.now());
      setSawProgress(false);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["status"] }),
    onError: (err) => {
      setStartedAt(null);
      setToastMsg(t("toast.error", { msg: err instanceof Error ? err.message : t("err.network") }));
    },
  });

  // Fresh status = fetched after we pressed Start (so a stale "calibrated" never counts)
  const fresh = startedAt !== null && statusQ.dataUpdatedAt > startedAt + 300;
  let phase: Phase = "intro";
  if (start.isPending || step === "sending") phase = "sending";
  else if (step === "bottom") phase = "bottom";
  else if (step === "top") phase = "top";
  else if (sawProgress && status?.calibrated) phase = "done";
  else if (fresh && !status?.calibrating) phase = "failed";

  const current = ORDER.indexOf(phase === "failed" ? "sending" : phase);

  return (
    <AppShell>
      <div className="page-head">
        <h1 className="page-title">{t("cal.title")}</h1>
        {status?.link && (
          <span className="pill">{status.link === "usb" ? t("link.pillUsb") : t("link.pillTtn")}</span>
        )}
      </div>
      <Link to="/settings" className="cal-back">
        <ArrowLeft size={16} aria-hidden="true" />
        <span>{t("nav.back")}</span>
      </Link>

      {phase !== "intro" && (
        <ol className="cal-steps" aria-label={t("cal.title")}>
          {STEPS.map((s, i) => {
            const idx = ORDER.indexOf(s.phase);
            const state = idx < current || phase === "done" ? "done" : idx === current ? "current" : "todo";
            return (
              <li key={s.phase} className={`cal-step ${state}`} aria-current={state === "current" ? "step" : undefined}>
                <span className="cal-step-dot">{state === "done" ? <Check size={14} weight="bold" /> : i + 1}</span>
                <span>{t(s.label)}</span>
              </li>
            );
          })}
        </ol>
      )}

      <section className="card cal-card" aria-live="polite">
        {phase === "intro" && (
          <>
            <p className="cal-lead">{t("cal.lead")}</p>
            <BoardButtons order={[]} />
            <ol className="cal-preview">
              <li>
                {t("cal.bottom1")} {t("cal.bottom2")}
              </li>
              <li>
                {t("cal.top1")} {t("cal.top2")}
              </li>
            </ol>
            {status?.calibrated && <p className="group-note">{t("cal.warn")}</p>}
            <button
              type="button"
              className="btn primary block"
              disabled={!status || start.isPending}
              onClick={() => start.mutate()}
            >
              {t("cal.start")}
            </button>
          </>
        )}

        {phase === "sending" && (
          <div className="cal-wait">
            <CircleNotch size={34} className="spin" aria-hidden="true" />
            <p className="cal-instruction">{t("cal.sending")}</p>
            {viaTtn && <p className="group-note">{t("cal.sendingTtn")}</p>}
          </div>
        )}

        {phase === "bottom" && (
          <>
            <BoardButtons order={["down", "stop"]} />
            <ol className="cal-do">
              <li>{t("cal.bottom1")}</li>
              <li>{t("cal.bottom2")}</li>
            </ol>
            <p className="group-note">{t("cal.tip")}</p>
          </>
        )}

        {phase === "top" && (
          <>
            <p className="cal-saved">
              <CheckCircle size={20} weight="fill" aria-hidden="true" /> {t("cal.bottomSaved")}
            </p>
            <BoardButtons order={["up", "stop"]} />
            <ol className="cal-do">
              <li>{t("cal.top1")}</li>
              <li>{t("cal.top2")}</li>
            </ol>
            <p className="group-note">{t("cal.tip")}</p>
          </>
        )}

        {phase === "done" && (
          <div className="cal-wait">
            <CheckCircle size={56} weight="fill" className="cal-ok" aria-hidden="true" />
            <p className="cal-instruction">{t("cal.done")}</p>
            <Link to="/" className="btn primary block">
              {t("cal.back")}
            </Link>
          </div>
        )}

        {phase === "failed" && (
          <div className="cal-wait">
            <WarningCircle size={40} weight="bold" className="cal-bad" aria-hidden="true" />
            <p className="cal-instruction">{t("cal.failed")}</p>
            <button type="button" className="btn primary block" onClick={() => start.mutate()}>
              {t("cal.start")}
            </button>
          </div>
        )}

        {(phase === "bottom" || phase === "top") && (
          <button
            type="button"
            className="btn ghost small cal-restart"
            disabled={start.isPending}
            onClick={() => start.mutate()}
          >
            {t("cal.restart")}
          </button>
        )}
      </section>

      <Toast message={toastMsg} onDismiss={() => setToastMsg(null)} />
    </AppShell>
  );
}
