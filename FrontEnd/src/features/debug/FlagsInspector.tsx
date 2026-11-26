import type { DecodedFlags } from "../../types/api";
import { toBinary8 } from "../../utils/formatters";

interface FlagsInspectorProps {
  flagsRaw: number;
  flagsHex: string;
  decoded: DecodedFlags;
}

const BITS: Array<{ bit: number; key: keyof DecodedFlags; label: string }> = [
  { bit: 0, key: "automatic", label: "automatic" },
  { bit: 1, key: "moving", label: "moving" },
  { bit: 2, key: "calibrated", label: "calibrated" },
  { bit: 3, key: "day", label: "day" },
  { bit: 4, key: "openBySun", label: "openBySun" },
  { bit: 5, key: "closeBySun", label: "closeBySun" },
  { bit: 6, key: "keepIndoorLight", label: "keepIndoorLight" },
];

export function FlagsInspector({ flagsRaw, flagsHex, decoded }: FlagsInspectorProps) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span className="row-sub">Flags byte:</span>
        <span className="hex-box">{flagsHex}</span>
        <span className="hex-box">0b{toBinary8(flagsRaw)}</span>
      </div>
      <div className="flags-grid">
        {BITS.map(({ bit, key, label }) => {
          const on = decoded[key];
          return (
            <span key={key} className={`pill mono ${on ? "ok" : ""}`}>
              b{bit}:{label}={on ? "1" : "0"}
            </span>
          );
        })}
      </div>
    </div>
  );
}
