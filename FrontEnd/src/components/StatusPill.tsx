import type { CommandStatus } from "../types/api";

export function StatusPill({ status }: { status: CommandStatus | string }) {
  let cls = "";
  if (status === "CONFIRMED") cls = "ok";
  else if (status === "IN_FLIGHT") cls = "warn";
  else if (status === "REJECTED" || status === "NOT_DELIVERED") cls = "danger";

  return <span className={`pill mono ${cls}`}>{status}</span>;
}
