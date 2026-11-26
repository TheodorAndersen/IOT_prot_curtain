interface ToastProps {
  message: string | null;
  actionLabel?: string;
  onAction?: () => void;
  onDismiss: () => void;
}

export function Toast({ message, actionLabel, onAction, onDismiss }: ToastProps) {
  if (!message) return null;
  return (
    <div className="toast" role="status" aria-live="polite">
      <p>{message}</p>
      {actionLabel && onAction && (
        <button
          type="button"
          className="btn small"
          onClick={() => {
            onDismiss();
            onAction();
          }}
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}
