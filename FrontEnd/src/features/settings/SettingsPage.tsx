import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Capacitor } from "@capacitor/core";
import { SignOut, Sun, SunDim } from "@phosphor-icons/react";
import { getApiBaseUrl, setApiBaseUrl } from "../../api/client";
import { api } from "../../api/endpoints";
import { AppShell } from "../../components/AppShell";
import { SaveBar } from "../../components/SaveBar";
import { Toast } from "../../components/Toast";
import { useAuth } from "../../hooks/useAuth";
import { useStatusPolling } from "../../hooks/useStatusPolling";
import { useTheme, type ThemeChoice } from "../../hooks/useTheme";
import { useI18n } from "../../i18n";
import { storage } from "../../utils/storage";

export function SettingsPage() {
  const { t, lang, setLang, pct } = useI18n();
  const { logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const queryClient = useQueryClient();

  const statusQ = useStatusPolling(false);
  const status = statusQ.data;

  const [thrOpen, setThrOpen] = useState(80);
  const [thrClose, setThrClose] = useState(10);
  const [setDirty, setSetDirty] = useState(false);
  const [clampErr, setClampErr] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  const [blindName, setBlindName] = useState(() => storage.get<string>("name", ""));
  const [instantSend, setInstantSend] = useState(() =>
    storage.get<boolean>("instantSend", false)
  );
  const [serverUrl, setServerUrl] = useState(getApiBaseUrl);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  useEffect(() => {
    if (status && !setDirty) {
      setThrOpen(status.outsideThresholdOpen);
      setThrClose(status.outsideThresholdClose);
    }
  }, [status, setDirty]);

  const mutation = useMutation({
    mutationFn: () =>
      api.putSettings({
        outsideThresholdOpen: thrOpen,
        outsideThresholdClose: thrClose,
      }),
    onSuccess: () => {
      setSetDirty(false);
      setClampErr("");
      setSavedFlash(true);
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      window.setTimeout(() => setSavedFlash(false), 1800);
    },
    onError: (err) => {
      setToastMsg(
        t("toast.error", { msg: err instanceof Error ? err.message : t("err.network") })
      );
    },
  });

  const handleOpenSlider = (val: number) => {
    let nextOpen = val;
    let err = "";
    if (nextOpen <= thrClose) {
      nextOpen = Math.min(100, thrClose + 1);
      err = t("sun.err");
    }
    setThrOpen(nextOpen);
    setClampErr(err);
    setSetDirty(true);
    setSavedFlash(false);
  };

  const handleCloseSlider = (val: number) => {
    let nextClose = val;
    let err = "";
    if (nextClose >= thrOpen) {
      nextClose = Math.max(0, thrOpen - 1);
      err = t("sun.err");
    }
    setThrClose(nextClose);
    setClampErr(err);
    setSetDirty(true);
    setSavedFlash(false);
  };

  const isStandalone =
    Capacitor.isNativePlatform() ||
    (typeof window !== "undefined" &&
      (window.matchMedia("(display-mode: standalone)").matches ||
        (navigator as Navigator & { standalone?: boolean }).standalone === true));

  return (
    <AppShell>
      <div className="page-head">
        <h1 className="page-title">{t("settings.title")}</h1>
      </div>

      <section className="group" aria-labelledby="h-blind">
        <h2 className="group-title" id="h-blind">
          {t("cal.settingsTitle")}
        </h2>
        <div className="card">
          <div className="row split">
            <div>
              <p style={{ margin: 0, fontWeight: 600 }}>
                {status?.calibrating
                  ? t("cal.statusBusy")
                  : status?.calibrated
                    ? t("cal.statusOk")
                    : t("cal.statusNo")}
              </p>
              <p className="group-note" style={{ margin: "2px 0 0" }}>
                {t("cal.settingsNote")}
              </p>
            </div>
            <Link to="/calibrate" className="btn primary small">
              {status?.calibrating ? t("cal.continue") : t("uncal.cta")}
            </Link>
          </div>
        </div>
      </section>

      <section className="group" aria-labelledby="h-sun">
        <h2 className="group-title" id="h-sun">
          {t("sun.title")}
        </h2>
        <div className="card">
          <div className="row">
            <div className="slider-head">
              <label htmlFor="thr-open">{t("sun.open")}</label>
              <output htmlFor="thr-open">{pct(thrOpen)}</output>
            </div>
            <div className="slider">
              <SunDim size={18} aria-hidden="true" />
              <input
                id="thr-open"
                type="range"
                min={1}
                max={100}
                step={1}
                value={thrOpen}
                onChange={(e) => handleOpenSlider(Number(e.target.value))}
              />
              <Sun size={22} aria-hidden="true" />
            </div>
          </div>

          <div className="row">
            <div className="slider-head">
              <label htmlFor="thr-close">{t("sun.close")}</label>
              <output htmlFor="thr-close">{pct(thrClose)}</output>
            </div>
            <div className="slider">
              <SunDim size={18} aria-hidden="true" />
              <input
                id="thr-close"
                type="range"
                min={0}
                max={99}
                step={1}
                value={thrClose}
                onChange={(e) => handleCloseSlider(Number(e.target.value))}
              />
              <Sun size={22} aria-hidden="true" />
            </div>
          </div>
        </div>
        <p className="group-note">{t("sun.note")}</p>
        {clampErr && (
          <p className="form-error" role="alert" style={{ marginTop: 6 }}>
            {clampErr}
          </p>
        )}
      </section>

      <section className="group" aria-labelledby="h-name">
        <h2 className="group-title" id="h-name">
          {t("name.title")}
        </h2>
        <div className="card">
          <div className="row">
            <input
              aria-label={t("name.title")}
              type="text"
              className="text-input"
              maxLength={28}
              placeholder={t("name.placeholder")}
              value={blindName}
              onChange={(e) => {
                setBlindName(e.target.value);
                storage.set("name", e.target.value.trim());
              }}
            />
          </div>
        </div>
        <p className="group-note">{t("name.note")}</p>
      </section>

      <section className="group" aria-labelledby="h-prefs">
        <h2 className="group-title" id="h-prefs">
          {t("prefs.title")}
        </h2>
        <div className="card">
          <div className="row">
            <p className="group-title" style={{ margin: "0 0 8px" }}>
              {t("prefs.lang")}
            </p>
            <div className="seg" role="radiogroup" aria-label={t("prefs.lang")}>
              <button
                type="button"
                role="radio"
                aria-checked={lang === "da"}
                className={`seg-btn${lang === "da" ? " active" : ""}`}
                onClick={() => setLang("da")}
              >
                Dansk
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={lang === "en"}
                className={`seg-btn${lang === "en" ? " active" : ""}`}
                onClick={() => setLang("en")}
              >
                English
              </button>
            </div>
          </div>

          <div className="row">
            <p className="group-title" style={{ margin: "0 0 8px" }}>
              {t("prefs.theme")}
            </p>
            <div className="seg" role="radiogroup" aria-label={t("prefs.theme")}>
              {(
                [
                  ["system", "prefs.themeSystem"],
                  ["light", "prefs.themeLight"],
                  ["dark", "prefs.themeDark"],
                ] as Array<[ThemeChoice, "prefs.themeSystem" | "prefs.themeLight" | "prefs.themeDark"]>
              ).map(([val, key]) => (
                <button
                  key={val}
                  type="button"
                  role="radio"
                  aria-checked={theme === val}
                  className={`seg-btn${theme === val ? " active" : ""}`}
                  onClick={() => setTheme(val)}
                >
                  {t(key)}
                </button>
              ))}
            </div>
          </div>

          <div className="row">
            <p className="group-title" style={{ margin: "0 0 8px" }}>
              {t("prefs.sendMode")}
            </p>
            <div className="seg" role="radiogroup" aria-label={t("prefs.sendMode")}>
              <button
                type="button"
                role="radio"
                aria-checked={!instantSend}
                className={`seg-btn${!instantSend ? " active" : ""}`}
                onClick={() => {
                  setInstantSend(false);
                  storage.set("instantSend", false);
                }}
              >
                {t("prefs.sendConfirm")}
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={instantSend}
                className={`seg-btn${instantSend ? " active" : ""}`}
                onClick={() => {
                  setInstantSend(true);
                  storage.set("instantSend", true);
                }}
              >
                {t("prefs.sendInstant")}
              </button>
            </div>
          </div>

          <div className="row">
            <div className="field">
              <label htmlFor="settings-server-url">{t("prefs.server")}</label>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  id="settings-server-url"
                  type="url"
                  value={serverUrl}
                  onChange={(e) => setServerUrl(e.target.value)}
                />
                <button
                  type="button"
                  className="btn outline"
                  onClick={() => {
                    setApiBaseUrl(serverUrl);
                    void queryClient.invalidateQueries();
                    setToastMsg(t("save.saved"));
                  }}
                >
                  {t("prefs.serverSave")}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      {!isStandalone && (
        <section className="group" aria-labelledby="h-install">
          <h2 className="group-title" id="h-install">
            {t("install.title")}
          </h2>
          <div className="card">
            <ol className="row" style={{ margin: 0, paddingLeft: "2rem" }}>
              <li>{t("install.s1")}</li>
              <li>{t("install.s2")}</li>
              <li>{t("install.s3")}</li>
            </ol>
          </div>
        </section>
      )}

      <section className="group">
        <div className="card">
          <button type="button" className="danger-btn" onClick={logout}>
            <SignOut size={20} aria-hidden="true" />
            <span>{t("logout")}</span>
          </button>
        </div>
      </section>

      <SaveBar
        visible={setDirty || savedFlash}
        text={
          savedFlash
            ? t("save.saved")
            : mutation.isPending
              ? t("save.saving")
              : t("save.unsaved")
        }
        cancelLabel={t("save.discard")}
        confirmLabel={t("save.save")}
        busy={mutation.isPending}
        saved={savedFlash}
        onCancel={() => {
          if (status) {
            setThrOpen(status.outsideThresholdOpen);
            setThrClose(status.outsideThresholdClose);
          }
          setClampErr("");
          setSetDirty(false);
        }}
        onConfirm={() => mutation.mutate()}
      />

      <Toast message={toastMsg} onDismiss={() => setToastMsg(null)} />
    </AppShell>
  );
}
