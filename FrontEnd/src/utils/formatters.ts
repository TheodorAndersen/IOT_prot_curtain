import type { DecodedFlags } from "../types/api";

export function closedToOpenPct(closedPct: number): number {
  return Math.max(0, Math.min(100, 100 - closedPct));
}

export function openToClosedPct(openPct: number): number {
  return Math.max(0, Math.min(100, 100 - openPct));
}

export function formatHexBytes(hex: string | null | undefined): string {
  if (!hex) return "–";
  const clean = hex.replace(/\s+/g, "").toUpperCase();
  return clean.replace(/(.{2})/g, "$1 ").trim();
}

export function toBinary8(val: number): string {
  return (val & 0xff).toString(2).padStart(8, "0");
}

export function decodeFlagsByte(rawFlags: number): DecodedFlags {
  return {
    automatic: Boolean(rawFlags & 0x01),
    moving: Boolean(rawFlags & 0x02),
    calibrated: Boolean(rawFlags & 0x04),
    day: Boolean(rawFlags & 0x08),
    openBySun: Boolean(rawFlags & 0x10),
    closeBySun: Boolean(rawFlags & 0x20),
    keepIndoorLight: Boolean(rawFlags & 0x40),
  };
}

export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 10000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export function formatRelativeAge(
  sec: number | null | undefined,
  lang: "da" | "en"
): string {
  if (sec === null || sec === undefined) return lang === "da" ? "aldrig" : "never";
  if (sec < 2) return lang === "da" ? "lige nu" : "just now";
  if (sec < 90) return lang === "da" ? `${Math.round(sec)} s siden` : `${Math.round(sec)} s ago`;
  const mins = Math.round(sec / 60);
  if (mins < 90) return lang === "da" ? `${mins} min siden` : `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return lang === "da" ? `${hrs} t siden` : `${hrs} h ago`;
}

export function formatLocalClock(
  isoString: string | null | undefined,
  lang: "da" | "en",
  withSeconds = false
): string {
  if (!isoString) return "–";
  const dt = new Date(isoString);
  if (Number.isNaN(dt.getTime())) return "–";
  return dt.toLocaleTimeString(lang === "da" ? "da-DK" : "en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
  });
}

export function formatLocalDateTime(
  isoString: string | null | undefined,
  lang: "da" | "en"
): string {
  if (!isoString) return "–";
  const dt = new Date(isoString);
  if (Number.isNaN(dt.getTime())) return "–";
  return dt.toLocaleString(lang === "da" ? "da-DK" : "en-GB", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
