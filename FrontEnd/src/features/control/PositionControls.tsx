import { useI18n } from "../../i18n";

interface PositionControlsProps {
  shownPosition: number | null;
  isMoving: boolean;
  disabled: boolean;
  onSelectPreset: (position: number) => void;
}

const PRESETS = [
  { p: 0, key: "preset.open" as const, aria: "preset.openAria" as const },
  { p: 50, key: "preset.half" as const, aria: "preset.halfAria" as const },
  { p: 100, key: "preset.closed" as const, aria: "preset.closedAria" as const },
];

export function PositionControls({
  shownPosition,
  isMoving,
  disabled,
  onSelectPreset,
}: PositionControlsProps) {
  const { t } = useI18n();

  return (
    <div className="presets" role="group" aria-label={t("preset.group")}>
      {PRESETS.map(({ p, key, aria }) => (
        <button
          key={p}
          type="button"
          className={`preset${shownPosition === p && !isMoving ? " current" : ""}`}
          aria-label={t(aria)}
          disabled={disabled}
          onClick={() => onSelectPreset(p)}
        >
          <span
            className="mini"
            style={{ "--m": p } as React.CSSProperties}
            aria-hidden="true"
          />
          <span>{t(key)}</span>
        </button>
      ))}
    </div>
  );
}
