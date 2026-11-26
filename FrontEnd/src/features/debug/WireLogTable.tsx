import { useMemo, useState } from "react";
import { StatusPill } from "../../components/StatusPill";
import type { DebugCommand, DebugUplink } from "../../types/api";
import {
  formatDurationMs,
  formatHexBytes,
  formatLocalClock,
} from "../../utils/formatters";

type LogFilter = "all" | "dl" | "ul" | "err";

interface LogEntry {
  uid: string;
  tsMs: number;
  tsIso: string;
  dir: "DL" | "UL";
  cmdId: number | null;
  badge: string;
  isError: boolean;
  description: string;
  meta: string;
  hex: string | null;
}

export function WireLogTable({
  title,
  commands,
  uplinks,
  lang,
}: {
  title: string;
  commands: DebugCommand[];
  uplinks: DebugUplink[];
  lang: "da" | "en";
}) {
  const [filter, setFilter] = useState<LogFilter>("all");

  const rows = useMemo(() => {
    const items: LogEntry[] = [];
    for (const c of commands) {
      const tsIso = c.sentAt ?? c.createdAt;
      items.push({
        uid: `cmd-${c.id}`,
        tsMs: new Date(tsIso).getTime(),
        tsIso,
        dir: "DL",
        cmdId: c.cmdId,
        badge: c.status,
        isError: c.status === "REJECTED" || c.status === "NOT_DELIVERED",
        description: c.description,
        meta:
          c.roundTripMs !== null
            ? formatDurationMs(c.roundTripMs)
            : `try ${c.attempts}`,
        hex: c.wirePayloadHex,
      });
    }
    for (const u of uplinks) {
      items.push({
        uid: `up-${u.id}`,
        tsMs: new Date(u.receivedAt).getTime(),
        tsIso: u.receivedAt,
        dir: "UL",
        cmdId: u.lastCmdId,
        badge: u.reason,
        isError: u.reason === "rejected",
        description: u.description,
        meta: u.position === null ? "uncal" : `${u.position}%`,
        hex: u.rawPayloadHex,
      });
    }
    items.sort((a, b) => b.tsMs - a.tsMs);
    return items.filter((item) => {
      if (filter === "dl") return item.dir === "DL";
      if (filter === "ul") return item.dir === "UL";
      if (filter === "err") return item.isError;
      return true;
    });
  }, [commands, uplinks, filter]);

  return (
    <section className="dcard debug-span-2">
      <div className="page-head" style={{ marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <div className="seg" role="tablist" aria-label="Wire log filter" style={{ maxWidth: 420 }}>
          {(
            [
              ["all", "All"],
              ["dl", "Commands (DL)"],
              ["ul", "Uplinks (UL)"],
              ["err", "Errors"],
            ] as Array<[LogFilter, string]>
          ).map(([val, label]) => (
            <button
              key={val}
              type="button"
              role="tab"
              aria-selected={filter === val}
              className={`seg-btn${filter === val ? " active" : ""}`}
              style={{ minHeight: 36, fontSize: "0.82rem", padding: "4px 10px" }}
              onClick={() => setFilter(val)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="wire-table-wrap">
        <table className="wire-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Dir</th>
              <th>Cmd</th>
              <th>Status / Reason</th>
              <th>Summary</th>
              <th>Detail</th>
              <th>Hex</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="row-sub">
                  No matching wire traffic yet.
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.uid}>
                  <td className="mono">{formatLocalClock(r.tsIso, lang, true)}</td>
                  <td>
                    <span className={`pill mono ${r.dir === "DL" ? "dl" : "ul"}`}>
                      {r.dir}
                    </span>
                  </td>
                  <td className="mono">{r.cmdId !== null ? `#${r.cmdId}` : "–"}</td>
                  <td>
                    {r.dir === "DL" ? (
                      <StatusPill status={r.badge} />
                    ) : (
                      <span className={`pill mono ${r.isError ? "danger" : "ul"}`}>
                        {r.badge}
                      </span>
                    )}
                  </td>
                  <td>{r.description}</td>
                  <td className="mono">{r.meta}</td>
                  <td className="mono">{formatHexBytes(r.hex)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
