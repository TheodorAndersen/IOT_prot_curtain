import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Capacitor } from "@capacitor/core";
import { Eye, EyeSlash } from "@phosphor-icons/react";
import { getApiBaseUrl, setApiBaseUrl } from "../../api/client";
import { api } from "../../api/endpoints";
import { useAuth } from "../../hooks/useAuth";
import { useI18n } from "../../i18n";

export function LoginPage() {
  const { t } = useI18n();
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [serverOpen, setServerOpen] = useState(() => Capacitor.isNativePlatform());
  const [serverUrl, setServerUrl] = useState(getApiBaseUrl);
  const [serverCheck, setServerCheck] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!email.trim() || !password) {
      setError(t("login.missing"));
      return;
    }
    setApiBaseUrl(serverUrl);
    setBusy(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("err.network");
      setError(t("login.failed", { msg }));
    } finally {
      setBusy(false);
    }
  };

  const handleTestServer = async () => {
    setServerCheck("");
    setApiBaseUrl(serverUrl);
    try {
      const h = await api.getHealth(serverUrl);
      setServerCheck(
        t("login.serverOk", {
          proto: h.protocolVersion,
          tx: h.transportConnected ? "connected" : "disconnected",
        })
      );
    } catch {
      setServerCheck(t("login.serverFail"));
    }
  };

  return (
    <main className="login-page">
      <div className="login-inner">
        <img className="login-icon" src="/icons/icon-192.png" alt="" />
        <h1 className="login-title">{t("login.title")}</h1>
        <p className="login-lead">{t("login.lead")}</p>

        <form className="login-form" onSubmit={handleSubmit} noValidate>
          <div className="field">
            <label htmlFor="login-email">{t("login.email")}</label>
            <input
              id="login-email"
              type="email"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="off"
              spellCheck={false}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="login-password">{t("login.password")}</label>
            <div className="pw">
              <input
                id="login-password"
                type={showPw ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <button
                type="button"
                className="pw-toggle"
                aria-label={t("login.show")}
                aria-pressed={showPw}
                onClick={() => setShowPw((v) => !v)}
              >
                {showPw ? <EyeSlash size={20} /> : <Eye size={20} />}
              </button>
            </div>
          </div>

          <div>
            <button
              type="button"
              className="btn ghost small"
              onClick={() => setServerOpen((v) => !v)}
            >
              {t("login.serverToggle")}
            </button>
            {serverOpen && (
              <div className="card" style={{ marginTop: 10 }}>
                <div className="row">
                  <div className="field">
                    <label htmlFor="server-url">{t("login.serverUrl")}</label>
                    <input
                      id="server-url"
                      type="url"
                      value={serverUrl}
                      onChange={(e) => setServerUrl(e.target.value)}
                    />
                  </div>
                  <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center" }}>
                    <button type="button" className="btn outline small" onClick={handleTestServer}>
                      {t("login.serverTest")}
                    </button>
                    {serverCheck && <span className="row-sub">{serverCheck}</span>}
                  </div>
                </div>
              </div>
            )}
          </div>

          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}

          <button className="btn primary block" type="submit" disabled={busy}>
            {busy ? t("login.busy") : t("login.submit")}
          </button>
        </form>
      </div>
    </main>
  );
}
