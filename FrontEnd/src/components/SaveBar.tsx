import { Check } from "@phosphor-icons/react";

interface SaveBarProps {
  visible: boolean;
  text: string;
  cancelLabel: string;
  confirmLabel: string;
  busy?: boolean;
  saved?: boolean;
  confirmDisabled?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function SaveBar({
  visible,
  text,
  cancelLabel,
  confirmLabel,
  busy = false,
  saved = false,
  confirmDisabled = false,
  onCancel,
  onConfirm,
}: SaveBarProps) {
  if (!visible) return null;

  return (
    <div className="savebar" role="region" aria-live="polite">
      <p className="savebar-text">
        {saved && <Check size={18} weight="bold" aria-hidden="true" />}
        <span>{text}</span>
      </p>
      {!saved && (
        <>
          <button type="button" className="btn ghost" disabled={busy} onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={busy || confirmDisabled}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </>
      )}
    </div>
  );
}
