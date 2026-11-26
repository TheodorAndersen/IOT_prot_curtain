import { useEffect, useState } from "react";
import { Copy } from "@phosphor-icons/react";
import { StatusPill } from "../../components/StatusPill";
import type { DebugCommand } from "../../types/api";
import {
  formatDurationMs,
  formatHexBytes,
  formatLocalDateTime,
} from "../../utils/formatters";

interface CommandCardProps {
  title: string;
  inFlight: DebugCommand | null;
  lastSent: DebugCommand | null;
  queued: DebugCommand[];
  handshakeTimeoutSec: number;
  handshakeMaxAttempts: number;
  lang: "da" | "en";
}

export function CommandCard({
  title,
  inFlight,
  lastSent,
  queued,
  handshakeTimeoutSec,
  handshakeMaxAttempts,
  lang,
}: CommandCardProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [copiedId, setCopiedId] = useState<number | null>(null);

  useEffect(() => {
    if (!inFlight?.sentAt) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [inFlight?.sentAt]);

  const elapsedSec =
    inFlight?.sentAt != null
      ? Math.max(0, Math.floor((nowMs - new Date(inFlight.sentAt).getTime()) / 1000))
      : 0;

  const copyHex = (id: number, hex: string) => {
    void navigator.clipboard?.writeText(formatHexBytes(hex)).catch(() => {});
    setCopiedId(id);
    window.setTimeout(() => setCopiedId(null), 1200);
  };

  return (
    <section className="dcard">
      <h2>
        <span>{title}</span>
        {inFlight && (
          <span className="pill warn mono">
            IN FLIGHT · {inFlight.attempts}/{handshakeMaxAttempts} ({elapsedSec}s /{" "}
            {handshakeTimeoutSec}s)
          </span>
        )}
      </h2>

      {inFlight && (
        <div className="banner warn" style={{ marginBottom: 12 }}>
          <div className="banner-body">
            <p>
              <strong>
                #{inFlight.cmdId ?? "?"} {inFlight.kind}
              </strong>{" "}
              — {inFlight.description}
            </p>
            {inFlight.wirePayloadHex && (
              <p className="mono" style={{ marginTop: 4 }}>
                FPort {inFlight.wireFport ?? 1} · {formatHexBytes(inFlight.wirePayloadHex)}
              </p>
            )}
          </div>
        </div>
      )}

      {lastSent ? (
        <div>
          <dl className="kv-list">
            <dt>Outcome</dt>
            <dd style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <StatusPill status={lastSent.status} />
              <span className="mono">
                #{lastSent.cmdId ?? "–"} · {lastSent.kind}
              </span>
            </dd>
            <dt>Description</dt>
            <dd>{lastSent.description}</dd>
            <dt>Attempts</dt>
            <dd className="mono">
              {lastSent.attempts} / {handshakeMaxAttempts}
            </dd>
            <dt>Sent</dt>
            <dd className="mono">{formatLocalDateTime(lastSent.sentAt, lang)}</dd>
            <dt>Resolved</dt>
            <dd className="mono">{formatLocalDateTime(lastSent.resolvedAt, lang)}</dd>
            <dt>Round-trip</dt>
            <dd className="mono">{formatDurationMs(lastSent.roundTripMs)}</dd>
            <dt>Wire payload</dt>
            <dd>
              {lastSent.wirePayloadHex ? (
                <span className="hex-box">
                  <span>
                    FPort {lastSent.wireFport ?? 1}: {formatHexBytes(lastSent.wirePayloadHex)}
                  </span>
                  <button
                    type="button"
                    className="btn ghost small"
                    aria-label="Copy command hex"
                    onClick={() => copyHex(lastSent.id, lastSent.wirePayloadHex ?? "")}
                  >
                    <Copy size={14} aria-hidden="true" />
                    <span>{copiedId === lastSent.id ? "Copied" : "Copy"}</span>
                  </button>
                </span>
              ) : (
                "–"
              )}
            </dd>
          </dl>
          <details style={{ marginTop: 10 }}>
            <summary className="row-sub" style={{ cursor: "pointer" }}>
              Decoded command args JSON
            </summary>
            <pre className="mono" style={{ fontSize: "0.78rem", overflowX: "auto", margin: "6px 0 0" }}>
              {JSON.stringify(lastSent.args, null, 2)}
            </pre>
          </details>
        </div>
      ) : (
        <p className="row-sub" style={{ margin: 0 }}>
          No command transmitted yet.
        </p>
      )}

      <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
        <p className="row-sub" style={{ margin: "0 0 6px", fontWeight: 600 }}>
          Queued ({queued.length})
        </p>
        {queued.length === 0 ? (
          <p className="row-sub" style={{ margin: 0 }}>
            Queue empty
          </p>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.85rem" }}>
            {queued.map((q) => (
              <li key={q.id} className="mono">
                {q.kind}: {JSON.stringify(q.args)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
