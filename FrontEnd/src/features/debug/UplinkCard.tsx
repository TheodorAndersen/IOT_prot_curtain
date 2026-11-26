import { useState } from "react";
import { Copy } from "@phosphor-icons/react";
import type { DebugUplink } from "../../types/api";
import {
  closedToOpenPct,
  formatHexBytes,
  formatLocalDateTime,
  formatRelativeAge,
} from "../../utils/formatters";
import { FlagsInspector } from "./FlagsInspector";

interface UplinkCardProps {
  title: string;
  uplink: DebugUplink | null;
  lang: "da" | "en";
}

export function UplinkCard({ title, uplink, lang }: UplinkCardProps) {
  const [copied, setCopied] = useState(false);

  const copyHex = (hex: string) => {
    void navigator.clipboard?.writeText(formatHexBytes(hex)).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <section className="dcard">
      <h2>
        <span>{title}</span>
        {uplink && <span className="pill ul mono">{uplink.reason}</span>}
      </h2>

      {uplink ? (
        <>
          <dl className="kv-list">
            <dt>Received</dt>
            <dd className="mono">
              {formatLocalDateTime(uplink.receivedAt, lang)} ({formatRelativeAge(uplink.ageSec, lang)})
            </dd>
            <dt>Last Cmd ID</dt>
            <dd className="mono">#{uplink.lastCmdId}</dd>
            <dt>Mode / Pos</dt>
            <dd>
              {uplink.mode} ·{" "}
              {uplink.position === null
                ? "uncalibrated"
                : `${closedToOpenPct(uplink.position)}% open (wire: ${uplink.position}% closed)`}
            </dd>
            <dt>Summary</dt>
            <dd>{uplink.description}</dd>
            <dt>Raw payload</dt>
            <dd>
              <span className="hex-box">
                <span>
                  FPort {uplink.rawFport}: {formatHexBytes(uplink.rawPayloadHex)}
                </span>
                <button
                  type="button"
                  className="btn ghost small"
                  aria-label="Copy uplink hex"
                  onClick={() => copyHex(uplink.rawPayloadHex)}
                >
                  <Copy size={14} aria-hidden="true" />
                  <span>{copied ? "Copied" : "Copy"}</span>
                </button>
              </span>
            </dd>
          </dl>

          <FlagsInspector
            flagsRaw={uplink.flags}
            flagsHex={uplink.flagsHex}
            decoded={uplink.decodedFlags}
          />
        </>
      ) : (
        <p className="row-sub" style={{ margin: 0 }}>
          No uplink received yet.
        </p>
      )}
    </section>
  );
}
