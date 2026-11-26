import { WarningCircle } from "@phosphor-icons/react";
import { useI18n } from "../i18n";
import { formatLocalClock } from "../utils/formatters";

interface BudgetBadgeProps {
  used: number;
  budget: number;
  nextSlotAt: string | null;
}

export function BudgetBadge({ used, budget, nextSlotAt }: BudgetBadgeProps) {
  const { t, lang } = useI18n();
  const severity = used >= budget ? "danger" : used >= Math.max(1, budget - 2) ? "warn" : "";
  const timeStr = formatLocalClock(nextSlotAt, lang);
  const title =
    used >= budget
      ? t("budget.reached", { budget, time: timeStr })
      : used >= Math.max(1, budget - 2)
        ? t("budget.warn", { budget })
        : undefined;

  return (
    <span className={`pill ${severity}`} title={title}>
      {severity && <WarningCircle size={14} weight="bold" aria-hidden="true" />}
      <span>{t("budget.label", { used, budget })}</span>
    </span>
  );
}
