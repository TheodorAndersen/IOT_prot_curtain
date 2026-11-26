import { Clock, Moon, Sun, SunDim, SunHorizon } from "@phosphor-icons/react";
import { useI18n } from "../../i18n";
import type { AutoRequest } from "../../types/api";

interface AutoRulesPanelProps {
  draft: AutoRequest;
  outsideThresholdOpen: number;
  outsideThresholdClose: number;
  thresholdClampError?: string;
  onChange: (next: AutoRequest) => void;
  onOutsideThresholdOpenChange: (value: number) => void;
  onOutsideThresholdCloseChange: (value: number) => void;
}

export function AutoRulesPanel({
  draft,
  outsideThresholdOpen,
  outsideThresholdClose,
  thresholdClampError = "",
  onChange,
  onOutsideThresholdOpenChange,
  onOutsideThresholdCloseChange,
}: AutoRulesPanelProps) {
  const { t, pct } = useI18n();

  return (
    <div>
      <section className="group" aria-labelledby="h-open">
        <h2 className="group-title" id="h-open">
          {t("auto.open")}
        </h2>
        <div className="card">
          <div className="row">
            <div className="seg" role="radiogroup" aria-labelledby="h-open">
              <button
                type="button"
                role="radio"
                aria-checked={draft.autoOpen === 0}
                className={`seg-btn${draft.autoOpen === 0 ? " active" : ""}`}
                onClick={() => onChange({ ...draft, autoOpen: 0 })}
              >
                <Clock size={18} aria-hidden="true" />
                <span>{t("auto.atTime")}</span>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={draft.autoOpen === 1}
                className={`seg-btn${draft.autoOpen === 1 ? " active" : ""}`}
                onClick={() => onChange({ ...draft, autoOpen: 1 })}
              >
                <SunHorizon size={18} aria-hidden="true" />
                <span>{t("auto.sunrise")}</span>
              </button>
            </div>
          </div>
          {draft.autoOpen === 0 ? (
            <div className="row split">
              <label htmlFor="open-time">{t("auto.time")}</label>
              <input
                id="open-time"
                type="time"
                className="time-input"
                value={draft.autoOpenTime}
                onChange={(e) => onChange({ ...draft, autoOpenTime: e.target.value })}
              />
            </div>
          ) : (
            <>
              <div className="row">
                <div className="slider-head">
                  <label htmlFor="auto-thr-open">{t("sun.open")}</label>
                  <output htmlFor="auto-thr-open">{pct(outsideThresholdOpen)}</output>
                </div>
                <div className="slider">
                  <SunDim size={18} aria-hidden="true" />
                  <input
                    id="auto-thr-open"
                    type="range"
                    min={1}
                    max={100}
                    step={1}
                    value={outsideThresholdOpen}
                    onChange={(e) => onOutsideThresholdOpenChange(Number(e.target.value))}
                  />
                  <Sun size={22} aria-hidden="true" />
                </div>
              </div>
              <p className="row row-sub" style={{ margin: 0 }}>
                {t("auto.sunriseNote", { o: pct(outsideThresholdOpen) })}
              </p>
            </>
          )}
        </div>
      </section>

      <section className="group" aria-labelledby="h-close">
        <h2 className="group-title" id="h-close">
          {t("auto.close")}
        </h2>
        <div className="card">
          <div className="row">
            <div className="seg" role="radiogroup" aria-labelledby="h-close">
              <button
                type="button"
                role="radio"
                aria-checked={draft.autoClose === 0}
                className={`seg-btn${draft.autoClose === 0 ? " active" : ""}`}
                onClick={() => onChange({ ...draft, autoClose: 0 })}
              >
                <Clock size={18} aria-hidden="true" />
                <span>{t("auto.atTime")}</span>
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={draft.autoClose === 1}
                className={`seg-btn${draft.autoClose === 1 ? " active" : ""}`}
                onClick={() => onChange({ ...draft, autoClose: 1 })}
              >
                <Moon size={18} aria-hidden="true" />
                <span>{t("auto.sunset")}</span>
              </button>
            </div>
          </div>
          {draft.autoClose === 0 ? (
            <div className="row split">
              <label htmlFor="close-time">{t("auto.time")}</label>
              <input
                id="close-time"
                type="time"
                className="time-input"
                value={draft.autoCloseTime}
                onChange={(e) => onChange({ ...draft, autoCloseTime: e.target.value })}
              />
            </div>
          ) : (
            <>
              <div className="row">
                <div className="slider-head">
                  <label htmlFor="auto-thr-close">{t("sun.close")}</label>
                  <output htmlFor="auto-thr-close">{pct(outsideThresholdClose)}</output>
                </div>
                <div className="slider">
                  <SunDim size={18} aria-hidden="true" />
                  <input
                    id="auto-thr-close"
                    type="range"
                    min={0}
                    max={99}
                    step={1}
                    value={outsideThresholdClose}
                    onChange={(e) => onOutsideThresholdCloseChange(Number(e.target.value))}
                  />
                  <Sun size={22} aria-hidden="true" />
                </div>
              </div>
              <p className="row row-sub" style={{ margin: 0 }}>
                {t("auto.sunsetNote", { c: pct(outsideThresholdClose) })}
              </p>
            </>
          )}
        </div>
        {thresholdClampError && (
          <p className="form-error" role="alert" style={{ marginTop: 6 }}>
            {thresholdClampError}
          </p>
        )}
      </section>

      <section className="group" aria-labelledby="h-keep">
        <h2 className="group-title" id="h-keep">
          {t("auto.keep")}
        </h2>
        <div className="card">
          <div className="row split">
            <label htmlFor="maintain-switch" className="row-label">
              <span>{t("auto.keepToggle")}</span>
              <span className="row-sub">{t("auto.keepDesc")}</span>
            </label>
            <input
              id="maintain-switch"
              type="checkbox"
              role="switch"
              className="switch"
              checked={draft.maintainLight === 1}
              onChange={(e) =>
                onChange({ ...draft, maintainLight: e.target.checked ? 1 : 0 })
              }
            />
          </div>
          {draft.maintainLight === 1 && (
            <div className="row">
              <div className="slider-head">
                <label htmlFor="target-light">{t("auto.level")}</label>
                <output htmlFor="target-light">{pct(draft.targetLightLevel)}</output>
              </div>
              <div className="slider">
                <SunDim size={18} aria-hidden="true" />
                <input
                  id="target-light"
                  type="range"
                  min={0}
                  max={100}
                  step={1}
                  value={draft.targetLightLevel}
                  onChange={(e) =>
                    onChange({ ...draft, targetLightLevel: Number(e.target.value) })
                  }
                />
                <Sun size={22} aria-hidden="true" />
              </div>
            </div>
          )}
        </div>
        <p className="group-note">{t("auto.keepNote")}</p>
      </section>
    </div>
  );
}
