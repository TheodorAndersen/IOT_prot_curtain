import React from "react";
import { NavLink } from "react-router-dom";
import { Bug, GearSix, Sliders, WifiSlash } from "@phosphor-icons/react";
import { useI18n } from "../i18n";
import { useNetworkStatus } from "../hooks/useNetworkStatus";
import { useStatusPolling } from "../hooks/useStatusPolling";
import { formatLocalClock } from "../utils/formatters";

export function AppShell({
  children,
  wide = false,
}: {
  children: React.ReactNode;
  wide?: boolean;
}) {
  const { t, lang } = useI18n();
  const networkUp = useNetworkStatus();
  const statusQ = useStatusPolling(false);

  const offline = !networkUp || statusQ.isError;
  const lastOkStr = statusQ.dataUpdatedAt
    ? formatLocalClock(new Date(statusQ.dataUpdatedAt).toISOString(), lang)
    : "";
  const isMoving = Boolean(statusQ.data?.moving);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink className="brand" to="/">
            <img src="/icons/icon-192.png" alt="" />
            <span>Smart Curtain</span>
          </NavLink>
          <nav className="topnav" aria-label="Main navigation">
            <NavLink
              to="/"
              end
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <Sliders size={18} weight="bold" aria-hidden="true" />
              <span>{t("nav.control")}</span>
            </NavLink>
            <NavLink
              to="/settings"
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <GearSix size={18} weight="bold" aria-hidden="true" />
              <span>{t("nav.settings")}</span>
            </NavLink>
            <NavLink
              to="/debug"
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
            >
              <Bug size={18} weight="bold" aria-hidden="true" />
              <span>{t("nav.debug")}</span>
              {isMoving && <span className="nav-dot" aria-label="active" />}
            </NavLink>
          </nav>
        </div>
      </header>

      <main className={`main-wrap${wide ? " wide" : ""}`}>
        {offline && (
          <div className="banner danger" role="status">
            <WifiSlash size={22} aria-hidden="true" />
            <div className="banner-body">
              <p>
                <strong>{t("offline.title")}</strong>{" "}
                {lastOkStr ? t("offline.since", { t: lastOkStr }) : ""}
              </p>
            </div>
            <button
              type="button"
              className="btn ghost small"
              onClick={() => void statusQ.refetch()}
            >
              {t("offline.retry")}
            </button>
          </div>
        )}
        {children}
      </main>

      <nav className="bottom-nav" aria-label="Mobile navigation">
        <NavLink
          to="/"
          end
          className={({ isActive }) => `bottom-tab${isActive ? " active" : ""}`}
        >
          <Sliders size={22} weight="bold" aria-hidden="true" />
          <span>{t("nav.control")}</span>
        </NavLink>
        <NavLink
          to="/settings"
          className={({ isActive }) => `bottom-tab${isActive ? " active" : ""}`}
        >
          <GearSix size={22} weight="bold" aria-hidden="true" />
          <span>{t("nav.settings")}</span>
        </NavLink>
        <NavLink
          to="/debug"
          className={({ isActive }) => `bottom-tab${isActive ? " active" : ""}`}
        >
          <Bug size={22} weight="bold" aria-hidden="true" />
          <span>{t("nav.debug")}</span>
          {isMoving && <span className="nav-dot" aria-hidden="true" />}
        </NavLink>
      </nav>
    </div>
  );
}
